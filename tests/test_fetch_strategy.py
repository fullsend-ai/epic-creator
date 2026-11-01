#!/usr/bin/env python3
"""Tests for scripts/fetch_strategy.py — strategy fetching and attachment handling."""
import errno
import io
import os
import runpy
import sys
import urllib.error
from http.client import BadStatusLine, HTTPResponse, IncompleteRead, RemoteDisconnected
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from fetch_strategy import (_find_strategy_attachment, _search_issues,
                            _write_strategy, cmd_fetch, cmd_fetch_one)
from jira_utils import JiraReadError, safe_error_message


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

    @staticmethod
    def _no_child_epics(server, path, user, token):
        key = parse_qs(urlsplit(path).query)["jql"][0].split()[2]
        return {"issues": [{"key": key}], "isLast": True}

    def test_skips_strategies_with_epics(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        issues = self._make_issues(3)

        def mock_has_epics(issue, s, u, t):
            return issue["key"] == "RHAISTRAT-2"

        ids_file = str(tmp_path / "ids.txt")
        with patch("fetch_strategy.require_env",
                   return_value=("s", "u", "t")), \
             patch("fetch_strategy.verify_jira_auth"), \
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
             patch("fetch_strategy.verify_jira_auth"), \
             patch("fetch_strategy._search_issues", return_value=issues), \
             patch("jira_utils.api_call_with_retry", side_effect=[
                 {"issues": [{"key": "RHAISTRAT-1"}], "isLast": True},
                 {"issues": [{"key": "RHOAIENG-100"}], "isLast": True},
             ]):
            from fetch_strategy import cmd_fetch
            cmd_fetch(["some jql", "--ids-file", str(ids_file),
                       "--skip-if-has-epics"])

        assert ids_file.read_text().splitlines() == ["RHAISTRAT-1"]
        strategy_dir = tmp_path / "artifacts" / "strat-tasks"
        assert (strategy_dir / "RHAISTRAT-1.md").exists()
        assert not (strategy_dir / "RHAISTRAT-2.md").exists()
        assert not (strategy_dir / "RHAISTRAT-3.md").exists()

    @pytest.mark.parametrize("lookup_result", [
        pytest.param({"fields": {}}, id="unresolved-type"),
        pytest.param(urllib.error.HTTPError(
            "https://jira.example.com/issue/RHAI-100", 403, "Forbidden", {}, None),
            id="forbidden"),
        pytest.param(urllib.error.HTTPError(
            "https://jira.example.com/issue/RHAI-100", 404, "Not Found", {}, None),
            id="not-found"),
        pytest.param(urllib.error.URLError("lookup failed"), id="network-error"),
        pytest.param(TimeoutError("lookup timed out"), id="timeout"),
        pytest.param(RemoteDisconnected("connection closed"), id="disconnected"),
        pytest.param(IncompleteRead(b"partial"), id="incomplete-read"),
        pytest.param(BadStatusLine("invalid status"), id="bad-status-line"),
        pytest.param(ConnectionResetError("connection reset"), id="connection-reset"),
        pytest.param(None, id="empty-response"),
    ])
    def test_failed_link_lookup_does_not_abort_batch(
            self, tmp_path, monkeypatch, capsys, lookup_result):
        monkeypatch.chdir(tmp_path)
        issues = self._make_issues(4)
        issues[1]["fields"]["issuelinks"] = [{
            "type": {"name": "Incorporates"},
            "outwardIssue": {"key": "RHAI-100"},
        }]
        issues[3]["fields"]["issuelinks"] = [{
            "type": {"name": "Incorporates"},
            "outwardIssue": {
                "key": "RHAI-101",
                "fields": {"issuetype": {"name": "Epic"}},
            },
        }]

        ids_file = tmp_path / "ids.txt"
        with patch("fetch_strategy.require_env", return_value=("s", "u", "t")), \
             patch("fetch_strategy.verify_jira_auth"), \
             patch("fetch_strategy._search_issues", return_value=issues), \
             patch("jira_utils.get_issue", side_effect=[lookup_result]), \
             patch("jira_utils.api_call_with_retry", side_effect=self._no_child_epics):
            from fetch_strategy import cmd_fetch
            cmd_fetch(["some jql", "--ids-file", str(ids_file),
                       "--skip-if-has-epics"])

        assert ids_file.read_text().splitlines() == ["RHAISTRAT-1", "RHAISTRAT-3"]
        strategy_dir = tmp_path / "artifacts" / "strat-tasks"
        assert sorted(path.name for path in strategy_dir.iterdir()) == [
            "RHAISTRAT-1.md", "RHAISTRAT-3.md",
        ]
        stderr = capsys.readouterr().err
        assert "WARNING: Skipped RHAISTRAT-2: could not check existing epics:" in stderr
        reason = (safe_error_message(lookup_result) if isinstance(lookup_result, Exception)
                  else "Cannot determine issue type for RHAI-100")
        assert reason in stderr
        assert "Skipped 1 strategies with existing epics" in stderr
        assert "Skipped 1 strategies because existing epics could not be checked" in stderr
        assert "Fetched 2 strategies" in stderr

    @pytest.mark.parametrize("error", [
        pytest.param(IncompleteRead(b"partial"), id="incomplete-read"),
        pytest.param(BadStatusLine("invalid status"), id="bad-status-line"),
    ])
    def test_failed_parent_check_does_not_abort_batch(
            self, tmp_path, monkeypatch, capsys, error):
        monkeypatch.chdir(tmp_path)
        ids_file = tmp_path / "ids.txt"
        with patch("fetch_strategy.require_env", return_value=("s", "u", "t")), \
             patch("fetch_strategy.verify_jira_auth"), \
             patch("fetch_strategy._search_issues", return_value=self._make_issues(3)), \
             patch("jira_utils.api_call_with_retry", side_effect=[
                 {"issues": [{"key": "RHAISTRAT-1"}], "isLast": True}, error,
                 {"issues": [{"key": "RHAISTRAT-3"}], "isLast": True},
             ]):
            from fetch_strategy import cmd_fetch
            cmd_fetch(["some jql", "--ids-file", str(ids_file),
                       "--skip-if-has-epics"])

        assert ids_file.read_text().splitlines() == ["RHAISTRAT-1", "RHAISTRAT-3"]
        strategy_dir = tmp_path / "artifacts" / "strat-tasks"
        assert sorted(path.name for path in strategy_dir.iterdir()) == [
            "RHAISTRAT-1.md", "RHAISTRAT-3.md",
        ]
        stderr = capsys.readouterr().err
        assert "WARNING: Skipped RHAISTRAT-2: could not check existing epics:" in stderr
        assert safe_error_message(error) in stderr
        assert "Fetched 2 strategies" in stderr

    @pytest.mark.parametrize("check_results, expected_exit", [
        pytest.param([
            urllib.error.URLError("lookup failed"),
            urllib.error.URLError("lookup failed"),
        ], 1, id="all-checks-failed"),
        pytest.param([
            {"issues": [{"key": "RHAI-100"}], "isLast": True},
            urllib.error.URLError("lookup failed"),
        ], 1, id="existing-epics-and-failed-check"),
        pytest.param([
            {"issues": [{"key": "RHAI-100"}], "isLast": True},
            {"issues": [{"key": "RHAI-101"}], "isLast": True},
        ], 0, id="all-have-epics"),
        pytest.param([], 0, id="no-matching-strategies"),
    ])
    def test_empty_batch_exit_status(
            self, tmp_path, monkeypatch, capsys, check_results, expected_exit):
        monkeypatch.chdir(tmp_path)
        ids_file = tmp_path / "ids.txt"
        ids_file.write_text("RHAISTRAT-9999\n")
        with patch("fetch_strategy.require_env", return_value=("s", "u", "t")), \
             patch("fetch_strategy.verify_jira_auth"), \
             patch("fetch_strategy._search_issues",
                   return_value=self._make_issues(len(check_results))), \
             patch("jira_utils.api_call_with_retry",
                   side_effect=check_results):
            from fetch_strategy import cmd_fetch
            args = ["some jql", "--ids-file", str(ids_file), "--skip-if-has-epics"]
            if expected_exit:
                with pytest.raises(JiraReadError, match="No strategies fetched"):
                    cmd_fetch(args)
            else:
                cmd_fetch(args)

        if expected_exit:
            assert not ids_file.exists()
        else:
            assert ids_file.read_text() == ""
        assert not (tmp_path / "artifacts" / "strat-tasks").exists()
        stderr = capsys.readouterr().err
        if expected_exit:
            failed_checks = sum(isinstance(result, Exception) for result in check_results)
            assert (f"Skipped {failed_checks} strategies because existing epics "
                    "could not be checked") in stderr
        else:
            assert "ERROR:" not in stderr

    def test_fetches_all_without_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        issues = self._make_issues(2)

        ids_file = str(tmp_path / "ids.txt")
        with patch("fetch_strategy.require_env",
                   return_value=("s", "u", "t")), \
             patch("fetch_strategy.verify_jira_auth"), \
             patch("fetch_strategy._search_issues",
                   return_value=issues):
            from fetch_strategy import cmd_fetch
            cmd_fetch(["some jql", "--ids-file", ids_file])

        with open(ids_file) as f:
            ids = f.read().strip().split("\n")
        assert ids == ["RHAISTRAT-1", "RHAISTRAT-2"]


class TestFetchSafety:
    def _issue(self, key="RHAISTRAT-9001"):
        return {"key": key, "fields": {
            "summary": "Strategy", "description": "Scoped work",
            "labels": [], "issuelinks": [], "attachment": [],
        }}

    def test_authenticated_empty_batch_replaces_stale_ids(self, tmp_path, capsys):
        ids_file = tmp_path / "ids.txt"
        ids_file.write_text("RHAISTRAT-9000\n")
        with patch("fetch_strategy.require_env", return_value=("s", "u", "t")), \
             patch("jira_utils.api_call_with_retry", side_effect=[
                 {"accountId": "test-account", "active": True},
                 {"issues": [], "isLast": True},
             ]), patch("fetch_strategy._write_strategy") as write_strategy:
            cmd_fetch(["some jql", "--ids-file", str(ids_file)])

        assert ids_file.read_text() == ""
        write_strategy.assert_not_called()
        assert "No work to decompose." in capsys.readouterr().err

    def test_expired_auth_removes_stale_ids_before_search(self, tmp_path):
        ids_file = tmp_path / "ids.txt"
        ids_file.write_text("RHAISTRAT-9000\n")
        with patch("fetch_strategy.require_env", return_value=("s", "u", "t")), \
             patch("fetch_strategy.verify_jira_auth",
                   side_effect=JiraReadError("authentication failed")), \
             patch("fetch_strategy._search_issues") as search:
            with pytest.raises(JiraReadError, match="authentication"):
                cmd_fetch(["some jql", "--ids-file", str(ids_file)])

        assert not ids_file.exists()
        search.assert_not_called()

    def test_missing_credentials_also_invalidate_old_ids(self, tmp_path):
        ids_file = tmp_path / "ids.txt"
        ids_file.write_text("RHAISTRAT-9000\n")
        with patch("fetch_strategy.require_env", return_value=("s", "u", None)):
            with pytest.raises(JiraReadError, match="JIRA_TOKEN must be set"):
                cmd_fetch(["some jql", "--ids-file", str(ids_file)])
        assert not ids_file.exists()

    def test_false_empty_child_check_publishes_only_verified_strategy(self, tmp_path, capsys):
        ids_file = tmp_path / "ids.txt"
        ids_file.write_text("RHAISTRAT-9000\n")
        with patch("fetch_strategy.require_env", return_value=("s", "u", "t")), \
             patch("jira_utils.api_call_with_retry", side_effect=[
                 {"accountId": "test-account", "active": True},
                 {"issues": [self._issue(), self._issue("RHAISTRAT-9002")],
                  "isLast": True},
                 {"issues": [{"key": "RHAISTRAT-9001"}], "isLast": True},
                 {"issues": [], "isLast": True},
             ]), patch("fetch_strategy._write_strategy") as write_strategy:
            cmd_fetch(["some jql", "--ids-file", str(ids_file),
                       "--skip-if-has-epics"])

        assert ids_file.read_text().splitlines() == ["RHAISTRAT-9001"]
        write_strategy.assert_called_once_with(self._issue(), server="s", user="u", token="t")
        stderr = capsys.readouterr().err
        assert "WARNING: Skipped RHAISTRAT-9002" in stderr
        assert "empty or incomplete" in stderr

    def test_failed_ids_publication_leaves_no_partial_file(self, tmp_path):
        ids_file = tmp_path / "ids.txt"
        with patch("fetch_strategy.require_env", return_value=("s", "u", "t")), \
             patch("fetch_strategy.verify_jira_auth"), \
             patch("fetch_strategy._search_issues", return_value=[self._issue()]), \
             patch("fetch_strategy._write_strategy"), \
             patch("fetch_strategy.os.replace", side_effect=OSError("write failed")):
            with pytest.raises(OSError):
                cmd_fetch(["some jql", "--ids-file", str(ids_file)])

        assert not ids_file.exists()
        assert not list(tmp_path.glob(".strategy-ids-*"))

    def test_fetch_one_reads_issue_without_redundant_auth_request(self, capsys):
        with patch("fetch_strategy.require_env", return_value=("s", "u", "t")), \
             patch("jira_utils.api_call_with_retry", return_value=self._issue()) as read, \
             patch("fetch_strategy._write_strategy", return_value="strategy.md"):
            cmd_fetch_one(["RHAISTRAT-9001"])

        read.assert_called_once()
        assert read.call_args.args[1].startswith("/issue/RHAISTRAT-9001?")
        assert capsys.readouterr().out.strip() == "strategy.md"

    @pytest.mark.parametrize("command", ["fetch", "fetch-one"])
    def test_cli_reports_auth_failure_and_recovery(
            self, tmp_path, monkeypatch, capsys, command):
        monkeypatch.chdir(tmp_path)
        script = os.path.join(os.path.dirname(__file__), "..", "scripts", "fetch_strategy.py")
        args = (["project = RHAISTRAT", "--ids-file", "ids.txt"]
                if command == "fetch" else ["RHAISTRAT-9001"])
        error = urllib.error.HTTPError("https://jira.example.com", 401,
                                       "private-response test-token", {}, None)
        with patch.dict(os.environ, {"JIRA_SERVER": "https://jira.example.com",
                                    "JIRA_USER": "bot@example.com", "JIRA_TOKEN": "test-token"}), \
             patch.object(sys, "argv", [script, command, *args]), \
             patch("urllib.request.urlopen", side_effect=error) as request:
            with pytest.raises(SystemExit) as failure:
                runpy.run_path(script, run_name="__main__")

        assert failure.value.code == 1
        output = capsys.readouterr().err
        assert "HTTP 401" in output
        assert "Stop this run and report the failure." in output
        assert "Check Jira access" in output
        assert "retry the same command with its checks enabled" in output
        assert "Do not continue with saved IDs or strategy artifacts." in output
        assert ("no IDs file published" in output) == (command == "fetch")
        assert "test-token" not in output
        assert "private-response" not in output
        assert not (tmp_path / "ids.txt").exists()
        assert not (tmp_path / "artifacts").exists()
        request.assert_called_once()

    def test_cli_identifies_failed_child_check_and_blocks_saved_inputs(
            self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "ids.txt").write_text("RHAISTRAT-9000\n")
        script = os.path.join(os.path.dirname(__file__), "..", "scripts", "fetch_strategy.py")
        with patch("jira_utils.require_env", return_value=("s", "u", "t")), \
             patch.object(sys, "argv", [script, "fetch", "project = RHAISTRAT",
                                        "--ids-file", "ids.txt", "--skip-if-has-epics"]), \
             patch("jira_utils.api_call_with_retry", side_effect=[
                 {"accountId": "test-account", "active": True},
                 {"issues": [self._issue()], "isLast": True},
                 {"issues": [], "isLast": True},
             ]):
            with pytest.raises(SystemExit) as failure:
                runpy.run_path(script, run_name="__main__")

        assert failure.value.code == 1
        output = capsys.readouterr().err
        assert "Existing-epic search for RHAISTRAT-9001 was empty or incomplete" in output
        assert "expected the known strategy alone in a complete result" in output
        assert "Fetch aborted; no IDs file published." in output
        assert "retry the same command with its checks enabled" in output
        assert "Do not continue with saved IDs or strategy artifacts." in output
        assert not (tmp_path / "ids.txt").exists()
        assert not (tmp_path / "artifacts").exists()

    def test_cli_reports_failed_publication_without_reusing_ids(
            self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        script = os.path.join(os.path.dirname(__file__), "..", "scripts", "fetch_strategy.py")
        with patch("jira_utils.require_env", return_value=("s", "u", "t")), \
             patch.object(sys, "argv", [script, "fetch", "project = RHAISTRAT",
                                        "--ids-file", "ids.txt"]), \
             patch("jira_utils.api_call_with_retry", side_effect=[
                 {"accountId": "test-account", "active": True},
                 {"issues": [], "isLast": True},
             ]), patch("os.replace", side_effect=PermissionError(
                 errno.EACCES, "private-write-detail test-token", str(tmp_path / "ids.txt"))):
            with pytest.raises(SystemExit) as failure:
                runpy.run_path(script, run_name="__main__")

        assert failure.value.code == 1
        output = capsys.readouterr().err
        assert "Permission denied (EACCES)" in output
        assert "private-write-detail" not in output
        assert "test-token" not in output
        assert "Fetch aborted; no IDs file published." in output
        assert "Stop this run and report the failure." in output
        assert not (tmp_path / "ids.txt").exists()
        assert not list(tmp_path.glob(".strategy-ids-*"))

    @pytest.mark.parametrize("server", [
        "http://bot:test-token@jira.example.com",
        "https://[malformed-test-token",
    ])
    def test_cli_explains_invalid_server_without_echoing_it(
            self, tmp_path, monkeypatch, capsys, server):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "ids.txt").write_text("RHAISTRAT-9000\n")
        script = os.path.join(os.path.dirname(__file__), "..", "scripts", "fetch_strategy.py")
        with patch.dict(os.environ, {"JIRA_SERVER": server,
                                    "JIRA_USER": "bot@example.com", "JIRA_TOKEN": "test-token"}), \
             patch.object(sys, "argv", [script, "fetch", "project = RHAISTRAT",
                                        "--ids-file", "ids.txt"]), \
             patch("urllib.request.urlopen") as request:
            with pytest.raises(SystemExit) as failure:
                runpy.run_path(script, run_name="__main__")

        assert failure.value.code == 1
        output = capsys.readouterr().err
        assert "JIRA_SERVER must be a valid HTTPS URL." in output
        assert "test-token" not in output
        assert server not in output
        assert "Fetch aborted; no IDs file published." in output
        assert "Stop this run and report the failure." in output
        assert not (tmp_path / "ids.txt").exists()
        request.assert_not_called()


class TestStrategySearchPagination:
    def test_reads_all_pages(self):
        first, second = {"key": "RHAISTRAT-9001"}, {"key": "RHAISTRAT-9002"}
        with patch("fetch_strategy.search_issues_page",
                   side_effect=[([first], "next-page"), ([second], None)]) as search:
            assert _search_issues("s", "u", "t", "some jql") == [first, second]
        assert search.call_args_list[1].kwargs["next_page_token"] == "next-page"

    @pytest.mark.parametrize("pages", [
        [([], "next-page")],
        [([{"key": "RHAISTRAT-9001"}], "same-page"),
         ([{"key": "RHAISTRAT-9002"}], "same-page")],
    ])
    def test_rejects_search_that_does_not_progress(self, pages):
        with patch("fetch_strategy.search_issues_page", side_effect=pages):
            with pytest.raises(JiraReadError, match="pagination progress"):
                _search_issues("s", "u", "t", "some jql")
