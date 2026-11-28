#!/usr/bin/env python3
"""Tests for repository mode in scripts/submit.py (ADR-0176). Jira is mocked."""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import yaml

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import repository_resolution  # noqa: E402
import submit  # noqa: E402
from repository_resolution import COMMENT_MARKER  # noqa: E402
from test_resolution_lookup import _snapshot  # noqa: E402
from test_resolution_verify import DASHBOARD_DOC, DASHBOARD_TEXT  # noqa: E402

FIELD = "customfield_10933"
QUOTE = "The ODH Dashboard is the web UI for OpenShift AI"
RESOLVED = "artifacts/repo-resolution/RHAISTRAT-9001-result.json"
UNRESOLVED = "artifacts/repo-resolution/RHAISTRAT-9001-E002-result.json"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A verified run on disk: manifest, two results, two epics."""
    monkeypatch.chdir(tmp_path)
    manifest, root = _snapshot(tmp_path / "src")
    (root / "architecture" / DASHBOARD_DOC).write_text(DASHBOARD_TEXT)
    monkeypatch.setattr(repository_resolution, "MANIFEST_PATH",
                        str(root / "manifest.json"))
    res = tmp_path / "artifacts/repo-resolution"
    res.mkdir(parents=True)
    evidence = {"source": "architecture", "path": DASHBOARD_DOC, "quote": QUOTE}
    pairs = {
        "RHAISTRAT-9001": {
            "status": "resolved", "phase1": {"skipped": "no-components"},
            "repositories": [{"key": "odh-dashboard",
                              "namespace": "red-hat-data-services",
                              "name": "odh-dashboard",
                              "url": "https://github.com/red-hat-data-services/odh-dashboard",
                              "hint": "none", "evidence": [evidence]}],
            "overridden_hints": []},
        "RHAISTRAT-9001-E002": {
            "status": "unresolved", "phase1": {"skipped": "no-components"},
            "reason": "No architecture document describes the billing service.",
            "candidates": [], "evidence": []},
    }
    for stem, result in pairs.items():
        (res / f"{stem}-request.json").write_text(
            json.dumps({"description": "work", "components": []}))
        (res / f"{stem}-result.json").write_text(json.dumps(result))
    epics_dir = tmp_path / "artifacts/epic-tasks"
    epics_dir.mkdir(parents=True)
    base = {"parent_strat": "RHAISTRAT-9001", "component": "Dashboard",
            "team": "UI", "type": "Implementation", "priority": "P1"}
    for num, key, path in [(1, "odh-dashboard", RESOLVED), (2, None, UNRESOLVED)]:
        epic_id = f"RHAISTRAT-9001-E{num:03d}"
        (epics_dir / f"{epic_id}.md").write_text("---\n" + yaml.safe_dump(
            {**base, "epic_id": epic_id, "title": f"Epic {num}",
             "repository_key": key, "repository_resolution": path})
            + "---\n## Description\nWork.\n")
    return tmp_path


def _plan(env):
    epics = submit._scan_epics("artifacts", "RHAISTRAT-9001")
    plan = submit._build_plan(epics, None)
    assert submit._attach_repositories("RHAISTRAT-9001", epics, plan) is None
    return plan


class TestPublication:
    def test_resolved_epic_gets_url_and_evidence(self, env):
        resolved = _plan(env)[0]["repository"]
        assert resolved["status"] == "resolved"
        assert resolved["url"] == "https://github.com/red-hat-data-services/odh-dashboard"
        assert resolved["field_id"] == FIELD
        assert QUOTE in resolved["evidence_md"]
        assert "a" * 40 in resolved["evidence_md"]  # the pinned source SHA
        assert DASHBOARD_DOC in resolved["evidence_md"]

    def test_unresolved_epic_gets_reason_and_warning(self, env):
        unresolved = _plan(env)[1]["repository"]
        assert unresolved["status"] == "unresolved" and unresolved["url"] is None
        assert COMMENT_MARKER in unresolved["comment_md"]
        assert "billing service" in unresolved["comment_md"]
        assert "**Candidates:**" in unresolved["comment_md"]

    def test_inconsistent_epic_blocks_the_strategy(self, env):
        path = env / "artifacts/epic-tasks/RHAISTRAT-9001-E001.md"
        path.write_text(path.read_text().replace("odh-dashboard", "kserve"))
        epics = submit._scan_epics("artifacts", "RHAISTRAT-9001")
        plan = submit._build_plan(epics, None)
        problem = submit._attach_repositories("RHAISTRAT-9001", epics, plan)
        assert "'kserve' is not in" in problem

    def test_missing_manifest_blocks_the_strategy(self, env, monkeypatch):
        monkeypatch.setattr(repository_resolution, "MANIFEST_PATH", "nope.json")
        epics = submit._scan_epics("artifacts", "RHAISTRAT-9001")
        plan = submit._build_plan(epics, None)
        assert "repository check failed" in \
            submit._attach_repositories("RHAISTRAT-9001", epics, plan)


class TestFieldCheck:
    def _api(self, fields_pages):
        def api_call(server, path, user, token, body=None, method=None):
            if path.endswith("/issuetypes"):
                return {"issueTypes": [{"id": "10000", "name": "Epic"}]}
            return fields_pages.pop(0)
        return api_call

    def test_url_field_passes(self):
        pages = [{"fields": [{"fieldId": FIELD, "schema": {
            "type": "string",
            "custom": "com.atlassian.jira.plugin.system.customfieldtypes:url"}}],
            "total": 1}]
        with patch.object(submit, "api_call", self._api(pages)):
            submit._check_repository_field("s", "u", "t", FIELD)

    def test_field_found_on_second_page(self):
        url = {"fieldId": FIELD, "schema": {"custom": "x:url"}}
        pages = [{"fields": [{"fieldId": "summary", "schema": {}}], "total": 2},
                 {"fields": [url], "total": 2}]
        with patch.object(submit, "api_call", self._api(pages)):
            submit._check_repository_field("s", "u", "t", FIELD)

    def test_non_url_field_is_rejected(self):
        pages = [{"fields": [{"fieldId": FIELD, "schema": {"custom": "x:textfield"}}],
                  "total": 1}]
        with patch.object(submit, "api_call", self._api(pages)):
            with pytest.raises(submit.JiraReadError, match="not a URL field"):
                submit._check_repository_field("s", "u", "t", FIELD)

    def test_missing_field_is_rejected(self):
        pages = [{"fields": [], "total": 0}]
        with patch.object(submit, "api_call", self._api(pages)):
            with pytest.raises(submit.JiraReadError, match="create screen"):
                submit._check_repository_field("s", "u", "t", FIELD)


class TestJiraWrites:
    def test_create_sets_url_field_only_for_resolved(self, env):
        plan = _plan(env)
        with patch.object(submit, "create_issue",
                          side_effect=["RHAI-1", "RHAI-2"]) as create:
            submit._create_epics("s", "u", "t", plan, "RHAISTRAT-9001")
        first, second = create.call_args_list
        assert first.kwargs["extra_fields"] == {
            FIELD: "https://github.com/red-hat-data-services/odh-dashboard"}
        assert second.kwargs["extra_fields"] is None

    def test_publish_attaches_evidence_and_warns(self, env):
        plan = _plan(env)
        keys = {"RHAISTRAT-9001-E001": "RHAI-1", "RHAISTRAT-9001-E002": "RHAI-2"}
        with patch.object(submit, "_get_existing_attachments", return_value={}), \
                patch.object(submit, "add_attachment") as attach, \
                patch.object(submit, "get_comments", return_value=[]), \
                patch.object(submit, "add_comment") as comment:
            assert submit._publish_repository_info("s", "u", "t", plan, keys) == 0
        attach.assert_called_once()
        assert attach.call_args.args[3:5] == ("RHAI-1", "RHAI-1-repository-evidence.md")
        comment.assert_called_once()
        assert comment.call_args.args[3] == "RHAI-2"

    def test_warning_comment_is_not_duplicated_on_rerun(self, env):
        plan = _plan(env)
        existing = [{"body": submit.markdown_to_adf(f"**{COMMENT_MARKER}**")}]
        with patch.object(submit, "get_comments", return_value=existing), \
                patch.object(submit, "add_comment") as comment:
            submit._publish_repository_info(
                "s", "u", "t", [plan[1]], {"RHAISTRAT-9001-E002": "RHAI-2"})
        comment.assert_not_called()

    def test_bad_field_blocks_all_writes(self, env):
        plan = _plan(env)
        with patch.object(submit, "verify_jira_auth"), \
                patch.object(submit, "get_issue",
                             return_value={"key": "RHAISTRAT-9001"}), \
                patch.object(submit, "has_existing_epics", return_value=False), \
                patch.object(submit, "_check_repository_field",
                             side_effect=submit.JiraReadError("not a URL field")), \
                patch.object(submit, "create_issue") as create:
            created, errors = submit._submit_strategy(
                "s", "u", "t", "RHAISTRAT-9001", plan, dry_run=False)
        assert (created, errors) == (0, 1)
        create.assert_not_called()

    def test_flag_off_plan_has_no_repository(self, env):
        epics = submit._scan_epics("artifacts", "RHAISTRAT-9001")
        plan = submit._build_plan(epics, None)
        assert all("repository" not in entry for entry in plan)
        with patch.object(submit, "create_issue",
                          side_effect=["RHAI-1", "RHAI-2"]) as create:
            submit._create_epics("s", "u", "t", plan, "RHAISTRAT-9001")
        assert all(c.kwargs["extra_fields"] is None for c in create.call_args_list)


class TestJiraUtils:
    def test_create_issue_merges_extra_fields(self):
        import jira_utils
        with patch.object(jira_utils, "api_call_with_retry",
                          return_value={"key": "RHAI-1"}) as call:
            jira_utils.create_issue("s", "u", "t", "RHAI", "Epic", "T", {}, "Major",
                                    extra_fields={FIELD: "https://github.com/a/b"})
        assert call.call_args.kwargs["body"]["fields"][FIELD] == "https://github.com/a/b"
