#!/usr/bin/env python3
"""Tests for scripts/fetch_strategy.py — strategy fetching and attachment handling."""
import io
import os
import sys
import urllib.error
from http.client import HTTPResponse
from unittest.mock import MagicMock, patch

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from fetch_strategy import (_find_strategy_attachment, _has_existing_epics,
                            _write_strategy)


class TestFindStrategyAttachment:
    """Tests for _find_strategy_attachment helper."""

    def test_matches_strategy_md(self):
        attachments = [
            {"filename": "RHAISTRAT-1234-strategy.md",
             "content": "https://jira.example.com/att/1", "size": 5000,
             "created": "2026-01-01T00:00:00Z"},
        ]
        result = _find_strategy_attachment(attachments, "RHAISTRAT-1234")
        assert result is not None
        fn, url, size = result
        assert fn == "RHAISTRAT-1234-strategy.md"
        assert size == 5000

    def test_ignores_review_files(self):
        attachments = [
            {"filename": "RHAISTRAT-1234-review.md",
             "content": "https://jira.example.com/att/1", "size": 3000,
             "created": "2026-01-01T00:00:00Z"},
        ]
        assert _find_strategy_attachment(attachments, "RHAISTRAT-1234") is None

    def test_ignores_non_md_files(self):
        attachments = [
            {"filename": "screenshot.png",
             "content": "https://jira.example.com/att/1", "size": 50000,
             "created": "2026-01-01T00:00:00Z"},
        ]
        assert _find_strategy_attachment(attachments, "RHAISTRAT-1234") is None

    def test_ignores_non_strategy_md(self):
        attachments = [
            {"filename": "some-notes.md",
             "content": "https://jira.example.com/att/1", "size": 5000,
             "created": "2026-01-01T00:00:00Z"},
        ]
        assert _find_strategy_attachment(attachments, "RHAISTRAT-1234") is None

    def test_returns_none_for_empty_list(self):
        assert _find_strategy_attachment([], "RHAISTRAT-1234") is None

    def test_returns_none_for_none(self):
        assert _find_strategy_attachment(None, "RHAISTRAT-1234") is None

    def test_picks_most_recent_revision(self):
        attachments = [
            {"filename": "RHAISTRAT-1234-strategy.md",
             "content": "https://jira.example.com/att/old", "size": 2000,
             "created": "2026-01-01T00:00:00Z"},
            {"filename": "RHAISTRAT-1234-strategy.md",
             "content": "https://jira.example.com/att/new", "size": 5000,
             "created": "2026-05-15T00:00:00Z"},
        ]
        fn, url, size = _find_strategy_attachment(attachments, "RHAISTRAT-1234")
        assert url == "https://jira.example.com/att/new"

    def test_does_not_match_bare_key_md(self):
        """Only matches -strategy.md, not bare RHAISTRAT-NNNN.md."""
        attachments = [
            {"filename": "RHAISTRAT-1234.md",
             "content": "https://jira.example.com/att/1", "size": 5000,
             "created": "2026-01-01T00:00:00Z"},
        ]
        assert _find_strategy_attachment(attachments, "RHAISTRAT-1234") is None


class TestWriteStrategyAttachment:
    """Tests for attachment integration in _write_strategy."""

    def _make_issue(self, key="RHAISTRAT-1234", description="Short desc",
                    attachments=None):
        return {
            "key": key,
            "fields": {
                "summary": "Test Strategy",
                "description": description,
                "labels": [],
                "issuelinks": [],
                "status": {"name": "Refined"},
                "priority": {"name": "Major"},
                "attachment": attachments or [],
            },
        }

    def test_uses_attachment_content(self, tmp_path):
        os.chdir(tmp_path)
        attach_body = b"# Full Strategy\n\nComplete content here."
        issue = self._make_issue(attachments=[
            {"filename": "RHAISTRAT-1234-strategy.md",
             "content": "https://jira.example.com/att/1", "size": len(attach_body),
             "created": "2026-01-01T00:00:00Z"},
        ])
        with patch("fetch_strategy.download_attachment", return_value=attach_body):
            path = _write_strategy(issue, output_dir=str(tmp_path / "out"),
                                   server="https://jira.example.com",
                                   user="u", token="t")
        with open(path) as f:
            content = f.read()
        assert "Complete content here." in content
        assert "Short desc" not in content

    def test_records_attachment_source_in_frontmatter(self, tmp_path):
        os.chdir(tmp_path)
        issue = self._make_issue(attachments=[
            {"filename": "RHAISTRAT-1234-strategy.md",
             "content": "https://jira.example.com/att/1", "size": 100,
             "created": "2026-01-01T00:00:00Z"},
        ])
        with patch("fetch_strategy.download_attachment", return_value=b"content"):
            path = _write_strategy(issue, output_dir=str(tmp_path / "out"),
                                   server="https://jira.example.com",
                                   user="u", token="t")
        with open(path) as f:
            raw = f.read()
        # Parse frontmatter between --- markers
        parts = raw.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["attachment_source"] == "RHAISTRAT-1234-strategy.md"

    def test_falls_back_to_description_without_credentials(self, tmp_path):
        os.chdir(tmp_path)
        issue = self._make_issue(attachments=[
            {"filename": "RHAISTRAT-1234-strategy.md",
             "content": "https://jira.example.com/att/1", "size": 100,
             "created": "2026-01-01T00:00:00Z"},
        ])
        # No server/user/token passed — should use description
        path = _write_strategy(issue, output_dir=str(tmp_path / "out"))
        with open(path) as f:
            content = f.read()
        assert "Short desc" in content

    def test_falls_back_to_description_on_download_error(self, tmp_path):
        os.chdir(tmp_path)
        issue = self._make_issue(attachments=[
            {"filename": "RHAISTRAT-1234-strategy.md",
             "content": "https://jira.example.com/att/1", "size": 100,
             "created": "2026-01-01T00:00:00Z"},
        ])
        with patch("fetch_strategy.download_attachment",
                   side_effect=ValueError("too large")):
            path = _write_strategy(issue, output_dir=str(tmp_path / "out"),
                                   server="https://jira.example.com",
                                   user="u", token="t")
        with open(path) as f:
            content = f.read()
        assert "Short desc" in content

    def test_no_attachment_source_without_attachment(self, tmp_path):
        os.chdir(tmp_path)
        issue = self._make_issue(attachments=[])
        path = _write_strategy(issue, output_dir=str(tmp_path / "out"))
        with open(path) as f:
            raw = f.read()
        parts = raw.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert "attachment_source" not in fm


class TestDownloadAttachmentValidation:
    """Tests for download_attachment security checks."""

    def test_rejects_http_url(self):
        from jira_utils import download_attachment
        with pytest.raises(ValueError, match="non-HTTPS"):
            download_attachment("http://jira.example.com/att/1", "u", "t")

    def test_rejects_file_url(self):
        from jira_utils import download_attachment
        with pytest.raises(ValueError, match="non-HTTPS"):
            download_attachment("file:///etc/passwd", "u", "t")

    def test_rejects_mismatched_host(self):
        from jira_utils import download_attachment
        with pytest.raises(ValueError, match="does not match"):
            download_attachment("https://evil.com/att/1", "u", "t",
                               server="https://jira.example.com")

    def test_rejects_oversized_content_length(self):
        from jira_utils import download_attachment
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Length": "2000000"}
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(ValueError, match="too large"):
                download_attachment("https://jira.example.com/att/1", "u", "t",
                                   max_bytes=1000)

    def test_rejects_oversized_response_body(self):
        from jira_utils import download_attachment
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.read.return_value = b"x" * 1001
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(ValueError, match="too large"):
                download_attachment("https://jira.example.com/att/1", "u", "t",
                                   max_bytes=1000)

    def test_does_not_retry_4xx(self):
        from jira_utils import download_attachment
        error = urllib.error.HTTPError(
            "https://jira.example.com/att/1", 403, "Forbidden", {}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                download_attachment("https://jira.example.com/att/1", "u", "t")
            assert exc_info.value.code == 403

    def test_retries_5xx(self):
        from jira_utils import download_attachment
        error = urllib.error.HTTPError(
            "https://jira.example.com/att/1", 502, "Bad Gateway", {}, None)
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.read.return_value = b"ok"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen",
                   side_effect=[error, mock_resp]) as mock_open:
            with patch("time.sleep"):  # skip wait
                result = download_attachment(
                    "https://jira.example.com/att/1", "u", "t")
        assert result == b"ok"
        assert mock_open.call_count == 2


class TestHasExistingEpics:
    """Tests for _has_existing_epics — checks Incorporates links and child items."""

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
        result = _has_existing_epics(issue, "s", "u", "t")
        assert result is True

    @pytest.mark.parametrize("issue_type", ["Outcome", "Feature", "Task"])
    def test_does_not_skip_for_incorporated_non_epic(self, issue_type):
        issue = self._make_issue(links=[self._incorporates_link(issue_type)])
        with patch("fetch_strategy.api_call_with_retry",
                   return_value={"issues": []}) as mock_api, \
             patch("fetch_strategy.get_issue") as mock_get:
            result = _has_existing_epics(issue, "s", "u", "t")
        assert result is False
        mock_api.assert_called_once_with(
            "s", "/search/jql?jql=parent%20%3D%20RHAISTRAT-1234%20AND%20"
            "issuetype%20%3D%20Epic&maxResults=1&fields=key", "u", "t")
        mock_get.assert_not_called()

    def test_checks_later_links_for_epics(self):
        issue = self._make_issue(links=[self._incorporates_link("Outcome"),
                                      self._incorporates_link("Epic")])
        assert _has_existing_epics(issue, "s", "u", "t") is True

    def test_checks_child_epics_after_non_epic_link(self):
        issue = self._make_issue(links=[self._incorporates_link("Outcome")])
        with patch("fetch_strategy.api_call_with_retry",
                   return_value={"issues": [{"key": "RHOAIENG-100"}]}):
            result = _has_existing_epics(issue, "s", "u", "t")
        assert result is True

    def test_returns_true_for_child_epics(self):
        issue = self._make_issue(links=[])
        with patch("fetch_strategy.api_call_with_retry",
                   return_value={"issues": [{"key": "RHOAIENG-100"}]}):
            result = _has_existing_epics(issue, "s", "u", "t")
        assert result is True

    def test_returns_false_when_no_epics(self):
        issue = self._make_issue(links=[])
        with patch("fetch_strategy.api_call_with_retry",
                   return_value={"issues": []}):
            result = _has_existing_epics(issue, "s", "u", "t")
        assert result is False

    def test_skips_api_calls_when_incorporated_epic_found(self):
        issue = self._make_issue(links=[self._incorporates_link()])
        with patch("fetch_strategy.api_call_with_retry") as mock_api, \
             patch("fetch_strategy.get_issue") as mock_get:
            _has_existing_epics(issue, "s", "u", "t")
        mock_api.assert_not_called()
        mock_get.assert_not_called()

    @pytest.mark.parametrize("issue_type, expected", [("Epic", True),
                                                      ("Outcome", False)])
    @pytest.mark.parametrize("fields", [{}, {"issuetype": None}])
    def test_fetches_missing_linked_issue_type(self, issue_type, expected,
                                             fields):
        link = self._incorporates_link()
        link["outwardIssue"]["fields"] = fields
        issue = self._make_issue(links=[link])
        with patch("fetch_strategy.get_issue", return_value={
            "fields": {"issuetype": {"name": issue_type}},
        }) as mock_get, \
             patch("fetch_strategy.api_call_with_retry",
                   return_value={"issues": []}) as mock_api:
            result = _has_existing_epics(issue, "s", "u", "t")
        assert result is expected
        mock_get.assert_called_once_with("s", "u", "t", "RHAI-100",
                                         fields=["issuetype"])
        assert mock_api.call_count == (0 if expected else 1)

    def test_reports_unresolved_linked_issue_type(self):
        link = self._incorporates_link()
        del link["outwardIssue"]["fields"]
        issue = self._make_issue(links=[link])
        with patch("fetch_strategy.get_issue", return_value={"fields": {}}), \
             pytest.raises(ValueError, match="RHAI-100.*RHAISTRAT-1234"):
            _has_existing_epics(issue, "s", "u", "t")

    def test_propagates_linked_issue_lookup_failure(self):
        link = self._incorporates_link()
        del link["outwardIssue"]["fields"]
        issue = self._make_issue(links=[link])
        with patch("fetch_strategy.get_issue", side_effect=urllib.error.URLError(
            "lookup failed")), pytest.raises(urllib.error.URLError):
            _has_existing_epics(issue, "s", "u", "t")

    def test_ignores_incoming_incorporates_link(self):
        link = self._incorporates_link()
        link["inwardIssue"] = link.pop("outwardIssue")
        issue = self._make_issue(links=[link])
        with patch("fetch_strategy.api_call_with_retry",
                   return_value={"issues": []}), \
             patch("fetch_strategy.get_issue") as mock_get:
            result = _has_existing_epics(issue, "s", "u", "t")
        assert result is False
        mock_get.assert_not_called()

    def test_ignores_non_incorporates_links(self):
        issue = self._make_issue(links=[{
            "type": {"name": "Blocks", "outward": "blocks"},
            "outwardIssue": {"key": "RHAI-200"},
        }])
        with patch("fetch_strategy.api_call_with_retry",
                   return_value={"issues": []}) as mock_api:
            result = _has_existing_epics(issue, "s", "u", "t")
        assert result is False
        mock_api.assert_called_once()


class TestSkipIfHasEpicsFlag:
    """Tests for --skip-if-has-epics flag in cmd_fetch."""

    def _make_issues(self, count=3):
        return [
            {"key": f"RHAISTRAT-{i}",
             "fields": {
                 "summary": f"Strategy {i}",
                 "description": f"Desc {i}",
                 "labels": [], "issuelinks": [],
                 "status": {"name": "Refined"},
                 "priority": {"name": "Major"},
                 "attachment": [],
             }}
            for i in range(1, count + 1)
        ]

    def test_skips_strategies_with_epics(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        issues = self._make_issues(3)

        def mock_has_epics(issue, s, u, t):
            return issue["key"] == "RHAISTRAT-2"

        ids_file = str(tmp_path / "ids.txt")
        with patch("fetch_strategy.require_env",
                   return_value=("s", "u", "t")), \
             patch("fetch_strategy._search_issues",
                   return_value=issues), \
             patch("fetch_strategy._has_existing_epics",
                   side_effect=mock_has_epics):
            from fetch_strategy import cmd_fetch
            cmd_fetch(["some jql", "--ids-file", ids_file,
                       "--skip-if-has-epics"])

        with open(ids_file) as f:
            ids = f.read().strip().split("\n")
        assert ids == ["RHAISTRAT-1", "RHAISTRAT-3"]

    def test_fetches_strategy_with_outcome_and_skips_epics(self, tmp_path,
                                                         monkeypatch):
        monkeypatch.chdir(tmp_path)
        issues = self._make_issues(3)
        for issue, issue_type in zip(issues, ["Outcome", "Epic", "Outcome"]):
            issue["fields"]["issuelinks"] = [{
                "type": {"name": "Incorporates", "outward": "incorporates"},
                "outwardIssue": {
                    "key": "RHAI-100",
                    "fields": {"issuetype": {"name": issue_type}},
                },
            }]

        ids_file = tmp_path / "ids.txt"
        with patch("fetch_strategy.require_env", return_value=("s", "u", "t")), \
             patch("fetch_strategy._search_issues", return_value=issues), \
             patch("fetch_strategy.api_call_with_retry", side_effect=[
                 {"issues": []}, {"issues": [{"key": "RHOAIENG-100"}]},
             ]):
            from fetch_strategy import cmd_fetch
            cmd_fetch(["some jql", "--ids-file", str(ids_file),
                       "--skip-if-has-epics"])

        assert ids_file.read_text().splitlines() == ["RHAISTRAT-1"]
        strategy_dir = tmp_path / "artifacts" / "strat-tasks"
        assert (strategy_dir / "RHAISTRAT-1.md").exists()
        assert not (strategy_dir / "RHAISTRAT-2.md").exists()
        assert not (strategy_dir / "RHAISTRAT-3.md").exists()

    def test_fetches_all_without_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        issues = self._make_issues(2)

        ids_file = str(tmp_path / "ids.txt")
        with patch("fetch_strategy.require_env",
                   return_value=("s", "u", "t")), \
             patch("fetch_strategy._search_issues",
                   return_value=issues):
            from fetch_strategy import cmd_fetch
            cmd_fetch(["some jql", "--ids-file", ids_file])

        with open(ids_file) as f:
            ids = f.read().strip().split("\n")
        assert ids == ["RHAISTRAT-1", "RHAISTRAT-2"]
