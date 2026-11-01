"""Regression tests for Jira reads that authorize epic decomposition."""

import base64
import errno
import io
import json
import os
import socket
import ssl
import sys
import urllib.error
from http.client import BadStatusLine, IncompleteRead
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from jira_utils import (JiraReadError, has_existing_epics, search_issues_page,
                        verify_jira_auth)


SERVER = "https://jira.example.com"
USER = "bot@example.com"
TOKEN = "synthetic-private-token"
STRAT = "RHAISTRAT-9001"
CHILD = "RHOAIENG-9002"
AUTH = base64.b64encode(f"{USER}:{TOKEN}".encode()).decode()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject_request(*args, **kwargs):
        raise AssertionError("Jira guard tests must not access the network")

    monkeypatch.setattr("urllib.request.urlopen", reject_request)


def _response(payload):
    response = MagicMock()
    response.status = 200
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__.return_value = response
    return response


def _strategy(links=None):
    return {"key": STRAT, "fields": {"issuelinks": links or []}}


class TestVerifyJiraAuth:
    def test_authenticates_same_user_and_token_against_myself(self):
        with patch("urllib.request.urlopen", return_value=_response({
            "accountId": "synthetic-account", "active": True,
        })) as request:
            assert verify_jira_auth(SERVER, USER, TOKEN) == "synthetic-account"

        sent = request.call_args.args[0]
        assert sent.full_url == f"{SERVER}/rest/api/3/myself"
        assert sent.get_header("Authorization") == f"Basic {AUTH}"
        assert sent.get_header("Accept") == "application/json"
        assert sent.get_method() == "GET"

    @pytest.mark.parametrize("identity", [
        None, [], {},
        {"accountId": "synthetic-account", "active": False},
        {"accountId": "synthetic-account"},
        {"accountId": " ", "active": True},
        {"accountId": 42, "active": True},
    ])
    def test_rejects_success_response_without_an_active_identity(self, identity):
        with patch("urllib.request.urlopen", return_value=_response(identity)):
            with pytest.raises(JiraReadError, match="identity"):
                verify_jira_auth(SERVER, USER, TOKEN)

    @pytest.mark.parametrize("status", [401, 403])
    def test_http_failure_does_not_expose_credentials_or_response_body(self, status, capsys):
        private_body = f"private-response-body {TOKEN} {AUTH}"
        error = urllib.error.HTTPError(
            f"{SERVER}/rest/api/3/myself", status, private_body, {},
            io.BytesIO(private_body.encode()),
        )
        with patch("urllib.request.urlopen", side_effect=error) as request:
            with pytest.raises(JiraReadError, match=f"HTTP {status}") as failure:
                verify_jira_auth(SERVER, USER, TOKEN)

        assert request.call_count == 1
        output = str(failure.value) + capsys.readouterr().err
        assert TOKEN not in output
        assert AUTH not in output
        assert "private-response-body" not in output

    def test_invalid_json_does_not_expose_response_body(self, capsys):
        response = _response(None)
        response.read.return_value = f"not-json {TOKEN} {AUTH}".encode()
        with patch("urllib.request.urlopen", return_value=response):
            with pytest.raises(JiraReadError) as failure:
                verify_jira_auth(SERVER, USER, TOKEN)

        output = str(failure.value) + capsys.readouterr().err
        assert "Invalid JSON response" in output
        assert TOKEN not in output
        assert AUTH not in output
        assert "not-json" not in output

    @pytest.mark.parametrize("error,category", [
        (urllib.error.URLError(socket.gaierror(-2, f"{TOKEN} {AUTH}")), "gaierror"),
        (urllib.error.URLError(ssl.SSLError(f"{TOKEN} {AUTH}")), "SSLError"),
        (urllib.error.URLError(TimeoutError(f"{TOKEN} {AUTH}")), "TimeoutError"),
        (urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED,
                                                      f"{TOKEN} {AUTH}")),
         "ConnectionRefusedError"),
        (urllib.error.URLError(f"private transport detail {TOKEN} {AUTH}"), "unknown cause"),
        (TimeoutError(f"{TOKEN} {AUTH}"), "TimeoutError"),
    ])
    def test_network_failure_has_safe_diagnostics(self, capsys, error, category):
        with patch("urllib.request.urlopen", side_effect=error), patch("time.sleep"):
            with pytest.raises(JiraReadError) as failure:
                verify_jira_auth(SERVER, USER, TOKEN)

        retries = capsys.readouterr().err
        assert category in str(failure.value)
        if isinstance(error, urllib.error.URLError):
            assert f"Network error ({category}), retrying" in retries
        output = str(failure.value) + retries
        assert TOKEN not in output
        assert AUTH not in output
        assert "private transport detail" not in output

    @pytest.mark.parametrize("error", [
        BadStatusLine(f"private-response {TOKEN}"), IncompleteRead(f"{TOKEN}".encode()),
    ])
    def test_http_protocol_failure_is_sanitized(self, capsys, error):
        with patch("urllib.request.urlopen", side_effect=error):
            with pytest.raises(JiraReadError) as failure:
                verify_jira_auth(SERVER, USER, TOKEN)
        output = str(failure.value) + capsys.readouterr().err
        assert type(error).__name__ in output
        assert TOKEN not in output
        assert "private-response" not in output


class TestSearchIssuesPage:
    def test_legitimate_terminal_empty_batch_is_allowed(self):
        with patch("jira_utils.api_call_with_retry", return_value={
            "issues": [], "isLast": True,
        }):
            assert search_issues_page(SERVER, USER, TOKEN, "project = RHAISTRAT") == ([], None)

    def test_preserves_query_and_pagination_token(self):
        jql = f"key = {STRAT} OR (parent = {STRAT} AND issuetype = Epic)"
        cursor = "cursor +/="
        issue = {"key": STRAT}
        with patch("jira_utils.api_call_with_retry", return_value={
            "issues": [issue], "isLast": False, "nextPageToken": "next-cursor",
        }) as request:
            result = search_issues_page(
                SERVER, USER, TOKEN, jql, max_results=2,
                fields="key,status", next_page_token=cursor,
            )

        assert result == ([issue], "next-cursor")
        server, path, user, token = request.call_args.args
        assert (server, user, token) == (SERVER, USER, TOKEN)
        parsed = urlsplit(path)
        assert parsed.path == "/search/jql"
        assert parse_qs(parsed.query) == {
            "jql": [jql], "maxResults": ["2"], "fields": ["key,status"],
            "nextPageToken": [cursor],
        }

    @pytest.mark.parametrize("payload", [
        None,
        {"isLast": True},
        {"issues": {}, "isLast": True},
        {"issues": [{}], "isLast": True},
        {"issues": [{"key": " "}], "isLast": True},
        {"issues": []},
        {"issues": [], "isLast": "true"},
        {"issues": [], "isLast": False},
        {"issues": [], "isLast": False, "nextPageToken": " "},
        {"issues": [], "isLast": True, "nextPageToken": "unexpected-cursor"},
    ])
    def test_ambiguous_search_data_is_not_a_valid_negative(self, payload):
        with patch("jira_utils.api_call_with_retry", return_value=payload):
            with pytest.raises(JiraReadError):
                search_issues_page(SERVER, USER, TOKEN, "project = RHAISTRAT")


class TestHasExistingEpics:
    def test_strategy_alone_in_complete_results_allows_decomposition(self):
        with patch("jira_utils.api_call_with_retry", return_value={
            "issues": [{"key": STRAT}], "isLast": True,
        }) as request:
            assert has_existing_epics(_strategy(), SERVER, USER, TOKEN) is False

        query = parse_qs(urlsplit(request.call_args.args[1]).query)
        assert query["jql"] == [
            f"key = {STRAT} OR (parent = {STRAT} AND issuetype = Epic)"]
        assert query["maxResults"] == ["2"]
        assert "project" not in query["jql"][0].lower()
        assert "status" not in query["jql"][0].lower()

    def test_empty_search_is_rejected_even_after_successful_authentication(self):
        with patch("jira_utils.api_call_with_retry", side_effect=[
            {"accountId": "synthetic-account", "active": True},
            {"issues": [], "isLast": True},
        ]):
            verify_jira_auth(SERVER, USER, TOKEN)
            with pytest.raises(JiraReadError, match="empty or incomplete"):
                has_existing_epics(_strategy(), SERVER, USER, TOKEN)

    def test_strategy_alone_on_nonterminal_page_is_inconclusive(self):
        with patch("jira_utils.api_call_with_retry", return_value={
            "issues": [{"key": STRAT}], "isLast": False,
            "nextPageToken": "another-page",
        }):
            with pytest.raises(JiraReadError, match="incomplete"):
                has_existing_epics(_strategy(), SERVER, USER, TOKEN)

    @pytest.mark.parametrize("issues,is_last", [
        ([{"key": STRAT}, {"key": CHILD, "fields": {
            "status": {"name": "Resolved"},
        }}], True),
        ([{"key": CHILD}], True),
        ([{"key": CHILD}, {"key": "RHAI-9003"}], False),
    ])
    def test_child_in_any_project_blocks_even_without_strategy_on_page(self, issues, is_last):
        page = {"issues": issues, "isLast": is_last}
        if not is_last:
            page["nextPageToken"] = "more-children"
        with patch("jira_utils.api_call_with_retry", return_value=page):
            assert has_existing_epics(_strategy(), SERVER, USER, TOKEN) is True

    def test_outward_incorporates_link_blocks_without_searching(self):
        link = {"type": {"name": "Incorporates"},
                "outwardIssue": {"key": CHILD, "fields": {"issuetype": {"name": "Epic"}}}}
        with patch("jira_utils.api_call_with_retry") as request:
            assert has_existing_epics(_strategy([link]), SERVER, USER, TOKEN) is True
        request.assert_not_called()

    @pytest.mark.parametrize("link", [None, "not-a-link", {"type": None}])
    def test_malformed_link_metadata_is_an_inconclusive_read(self, link):
        with patch("jira_utils.api_call_with_retry") as request:
            with pytest.raises(JiraReadError):
                has_existing_epics(_strategy([link]), SERVER, USER, TOKEN)
        request.assert_not_called()

    @pytest.mark.parametrize("link", [
        {"type": {"name": "Incorporates"}, "inwardIssue": {"key": CHILD}},
        {"type": {"name": "Blocks"}, "outwardIssue": {"key": CHILD}},
    ])
    def test_other_links_do_not_replace_child_search(self, link):
        with patch("jira_utils.api_call_with_retry", return_value={
            "issues": [{"key": STRAT}], "isLast": True,
        }) as request:
            assert has_existing_epics(_strategy([link]), SERVER, USER, TOKEN) is False
        request.assert_called_once()


class TestIncorporatedIssueTypes:
    """Preserve issue-type checks when moving the legacy guard into jira_utils."""

    def _make_issue(self, key="RHAISTRAT-1234", links=None):
        return {
            "key": key,
            "fields": {"issuelinks": links or []},
        }

    def _incorporates_link(self, issue_type="Epic"):
        return {
            "type": {"name": "Incorporates", "outward": "incorporates"},
            "outwardIssue": {
                "key": "RHAI-100",
                "fields": {"issuetype": {"name": issue_type}},
            },
        }

    def test_returns_true_for_incorporated_epic(self):
        issue = self._make_issue(links=[self._incorporates_link()])
        result = has_existing_epics(issue, "s", "u", "t")
        assert result is True

    @pytest.mark.parametrize("issue_type", ["Outcome", "Feature", "Task"])
    def test_does_not_skip_for_incorporated_non_epic(self, issue_type):
        issue = self._make_issue(links=[self._incorporates_link(issue_type)])
        with patch("jira_utils.api_call_with_retry",
                   return_value={"issues": [{"key": "RHAISTRAT-1234"}], "isLast": True}) as mock_api, \
             patch("jira_utils.get_issue") as mock_get:
            result = has_existing_epics(issue, "s", "u", "t")
        assert result is False
        mock_api.assert_called_once()
        mock_get.assert_not_called()

    def test_checks_later_links_for_epics(self):
        issue = self._make_issue(links=[self._incorporates_link("Outcome"),
                                      self._incorporates_link("Epic")])
        assert has_existing_epics(issue, "s", "u", "t") is True

    def test_checks_child_epics_after_non_epic_link(self):
        issue = self._make_issue(links=[self._incorporates_link("Outcome")])
        with patch("jira_utils.api_call_with_retry",
                   return_value={"issues": [{"key": "RHOAIENG-100"}], "isLast": True}):
            result = has_existing_epics(issue, "s", "u", "t")
        assert result is True

    def test_returns_true_for_child_epics(self):
        issue = self._make_issue(links=[])
        with patch("jira_utils.api_call_with_retry",
                   return_value={"issues": [{"key": "RHOAIENG-100"}], "isLast": True}):
            result = has_existing_epics(issue, "s", "u", "t")
        assert result is True

    def test_returns_false_when_no_epics(self):
        issue = self._make_issue(links=[])
        with patch("jira_utils.api_call_with_retry",
                   return_value={"issues": [{"key": "RHAISTRAT-1234"}], "isLast": True}):
            result = has_existing_epics(issue, "s", "u", "t")
        assert result is False

    def test_skips_api_calls_when_incorporated_epic_found(self):
        issue = self._make_issue(links=[self._incorporates_link()])
        with patch("jira_utils.api_call_with_retry") as mock_api, \
             patch("jira_utils.get_issue") as mock_get:
            has_existing_epics(issue, "s", "u", "t")
        mock_api.assert_not_called()
        mock_get.assert_not_called()

    @pytest.mark.parametrize("issue_type, expected", [("Epic", True),
                                                      ("Outcome", False)])
    @pytest.mark.parametrize("fields", [
        {}, None, [], {"issuetype": None}, {"issuetype": []},
        {"issuetype": {"name": 123}}, {"issuetype": {"name": []}},
        {"issuetype": {"name": " "}},
    ])
    def test_fetches_missing_linked_issue_type(self, issue_type, expected,
                                             fields):
        link = self._incorporates_link()
        link["outwardIssue"]["fields"] = fields
        issue = self._make_issue(links=[link])
        with patch("jira_utils.get_issue", return_value={
            "fields": {"issuetype": {"name": issue_type}},
        }) as mock_get, \
             patch("jira_utils.api_call_with_retry",
                   return_value={"issues": [{"key": "RHAISTRAT-1234"}], "isLast": True}) as mock_api:
            result = has_existing_epics(issue, "s", "u", "t")
        assert result is expected
        mock_get.assert_called_once_with("s", "u", "t", "RHAI-100",
                                         fields=["issuetype"])
        assert mock_api.call_count == (0 if expected else 1)

    def test_reports_unresolved_linked_issue_type(self):
        link = self._incorporates_link()
        del link["outwardIssue"]["fields"]
        issue = self._make_issue(links=[link])
        with patch("jira_utils.get_issue", return_value={"fields": {}}), \
             pytest.raises(JiraReadError, match="RHAI-100.*RHAISTRAT-1234"):
            has_existing_epics(issue, "s", "u", "t")

    def test_reports_linked_issue_lookup_failure(self):
        link = self._incorporates_link()
        del link["outwardIssue"]["fields"]
        issue = self._make_issue(links=[link])
        with patch("jira_utils.get_issue", side_effect=urllib.error.URLError(
            "lookup failed")), pytest.raises(JiraReadError, match="unknown cause"):
            has_existing_epics(issue, "s", "u", "t")

    def test_ignores_incoming_incorporates_link(self):
        link = self._incorporates_link()
        link["inwardIssue"] = link.pop("outwardIssue")
        issue = self._make_issue(links=[link])
        with patch("jira_utils.api_call_with_retry",
                   return_value={"issues": [{"key": "RHAISTRAT-1234"}], "isLast": True}), \
             patch("jira_utils.get_issue") as mock_get:
            result = has_existing_epics(issue, "s", "u", "t")
        assert result is False
        mock_get.assert_not_called()

    def test_ignores_non_incorporates_links(self):
        issue = self._make_issue(links=[{
            "type": {"name": "Blocks", "outward": "blocks"},
            "outwardIssue": {"key": "RHAI-200"},
        }])
        with patch("jira_utils.api_call_with_retry",
                   return_value={"issues": [{"key": "RHAISTRAT-1234"}], "isLast": True}) as mock_api:
            result = has_existing_epics(issue, "s", "u", "t")
        assert result is False
        mock_api.assert_called_once()

    @pytest.mark.parametrize("linked_issue", [None, {}, {"key": None}, {"key": "invalid"}])
    def test_invalid_linked_key_cannot_establish_absence_of_epics(self, linked_issue):
        issue = self._make_issue(links=[{
            "type": {"name": "Incorporates"}, "outwardIssue": linked_issue,
        }])
        with patch("jira_utils.api_call_with_retry") as request:
            with pytest.raises(JiraReadError):
                has_existing_epics(issue, "s", "u", "t")
        request.assert_not_called()

    @pytest.mark.parametrize("fields", [
        {"issuetype": {"name": 123}}, {"issuetype": {"name": []}},
        {"issuetype": {"name": " "}},
    ])
    def test_malformed_fallback_type_is_inconclusive(self, fields):
        link = self._incorporates_link()
        del link["outwardIssue"]["fields"]
        issue = self._make_issue(links=[link])
        with patch("jira_utils.get_issue", return_value={"fields": fields}), \
             patch("jira_utils.api_call_with_retry") as search:
            with pytest.raises(JiraReadError):
                has_existing_epics(issue, "s", "u", "t")
        search.assert_not_called()
