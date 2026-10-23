"""Contract tests for descriptor validation and opt-in Initiative intake.

Jira reads/downloads are the only doubles: selection, writing and validation
run against the real registry and filesystem. All issue data is synthetic.
"""
import importlib
import importlib.util
from pathlib import Path
import sys
import shutil
import urllib.error
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import artifact_utils
import fetch_strategy


def registry_module():
    assert importlib.util.find_spec("workload") is not None, "Workload registry is missing"
    return importlib.import_module("workload")


def issue(key="RHOAIENG-9001", project="RHOAIENG", issue_type="Initiative"):
    return {"key": key, "fields": {
        "project": {"key": project}, "issuetype": {"name": issue_type},
        "summary": "Synthetic Initiative", "description": "Initiative text, not strategy",
        "labels": ["strat-creator-rubric-pass", "strat-creator-human-sign-off"],
        "issuelinks": [], "status": {"name": "Refined"},
        "priority": {"name": "Major"}, "updated": "2026-10-13T10:00:00Z",
        "attachment": [{"id": "9001", "filename": f"{key}-strategy.md",
                        "content": "https://jira.example.com/att/new", "size": 20,
                        "created": "2026-10-13T09:00:00Z"}],
    }}


def descriptor():
    return {"schema_version": 1, "id": "synthetic-initiative", "version": 1,
            "source": {"project": "EXAMPLE", "issue_type": "Initiative",
                       "id_pattern": r"^EXAMPLE-\d+$",
                       "attachment_template": "{key}-strategy.md",
                       "description_fallback": False,
                       "ready_labels": ["approved"], "skip_policy": "processed-label",
                       "processed_label": "synthetic-processed",
                       "handoff_type": "initiative-strategy",
                       "identity_fields": ["strat_id", "jira_key", "source_initiative"]}}


def test_resolves_initiative_by_tracker_binding():
    module = registry_module()
    selected = module.resolve_issue(issue())
    assert selected.id == "rhai-initiative"
    assert selected.version == 1


@pytest.mark.parametrize("project,issue_type,key", [
    ("OTHER", "Initiative", "RHOAIENG-9001"),
    ("RHOAIENG", "Epic", "RHOAIENG-9001"),
    ("RHOAIENG", "Initiative", "RHAISTRAT-9001"),
    ("RHOAIENG", "Initiative", "../RHOAIENG-9001"),
])
def test_rejects_unknown_or_conflicting_tracker_identity(project, issue_type, key):
    module = registry_module()
    with pytest.raises(module.WorkloadError):
        module.resolve_issue(issue(key, project, issue_type))


def test_explicit_workload_cannot_override_tracker_binding():
    module = registry_module()
    with pytest.raises(module.WorkloadError):
        module.resolve_issue(issue(), workload_id="rhaistrat-strategy")


@pytest.mark.parametrize("field,value", [
    ("schema_version", 2), ("version", 2), ("id", "../escape"),
    ("source", {}), ("plugin", "exec.py"),
])
def test_rejects_invalid_descriptors_before_use(tmp_path, field, value):
    module = registry_module()
    data = descriptor()
    data[field] = value
    (tmp_path / "type.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(module.WorkloadError):
        module.load_registry(tmp_path)


@pytest.mark.parametrize("field,value", [
    ("attachment_template", "../../{key}.md"), ("skip_policy", "custom-plugin"),
    ("id_pattern", "["), ("id_pattern", "EXAMPLE"), ("ready_labels", []),
])
def test_rejects_unsupported_or_unsafe_intake_policy(tmp_path, field, value):
    module = registry_module()
    data = descriptor()
    data["source"][field] = value
    (tmp_path / "type.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(module.WorkloadError):
        module.load_registry(tmp_path)


@pytest.mark.parametrize("duplicate", ["id", "binding"])
def test_duplicate_registry_entries_fail(tmp_path, duplicate):
    module = registry_module()
    first, second = descriptor(), descriptor()
    if duplicate == "binding":
        second["id"] = "other-initiative"
    (tmp_path / "one.yaml").write_text(yaml.safe_dump(first))
    (tmp_path / "two.yaml").write_text(yaml.safe_dump(second))
    with pytest.raises(module.WorkloadError):
        module.load_registry(tmp_path)


def test_new_binding_can_resolve_from_an_added_descriptor(tmp_path):
    module = registry_module()
    (tmp_path / "example.yaml").write_text(yaml.safe_dump(descriptor()))
    selected = module.resolve_issue(issue("EXAMPLE-9001", "EXAMPLE", "Initiative"),
                                    registry=module.load_registry(tmp_path))
    assert selected.id == "synthetic-initiative"


@pytest.mark.parametrize("newest,older", [
    ("2026-10-13T09:00:00Z", "2026-10-13T10:00:00+02:00"),
    ("2026-10-13T09:00:00.000+0000", "2026-10-13T10:00:00.000+0200"),
    ("2026-10-13T04:00:00.000-0500", "2026-10-13T10:00:00.000+0200"),
])
def test_initiative_uses_newest_exact_key_attachment(tmp_path, newest, older):
    module = registry_module()
    data = issue()
    data["fields"]["attachment"][0]["created"] = newest
    data["fields"]["attachment"] += [
        {"id": "9002", "filename": "RHOAIENG-9001-strategy.md",
         "content": "https://jira.example.com/att/old", "size": 20,
         "created": older},
        {"id": "9003", "filename": "RHOAIENG-9002-strategy.md",
         "content": "https://jira.example.com/att/wrong", "size": 20,
         "created": "2026-10-13T12:00:00Z"},
    ]

    def download(url, user, token, server):
        assert url == "https://jira.example.com/att/new"
        return b"# Approved synthetic strategy\n"

    with patch("fetch_strategy.download_attachment", side_effect=download):
        path = fetch_strategy._write_strategy(
            data, str(tmp_path), "https://jira.example.com", "u", "t",
            workload=module.resolve_issue(data))
    fm, body = artifact_utils.read_frontmatter_validated(path, "strat-task")
    assert "Approved synthetic strategy" in body
    assert "Initiative text" not in body
    assert fm["strat_id"] == "RHOAIENG-9001"
    assert fm["workload_id"] == "rhai-initiative"
    assert fm["workload_version"] == 1
    assert fm["source_key"] == "RHOAIENG-9001"
    assert fm["source_project"] == "RHOAIENG"
    assert fm["source_issue_type"] == "Initiative"
    assert fm["source_revision"] == "2026-10-13T10:00:00Z"
    assert fm["attachment_id"] == "9001"
    assert fm["attachment_created"] == newest


def test_missing_initiative_attachment_skips_without_writing(tmp_path):
    module = registry_module()
    data = issue()
    data["fields"]["attachment"] = []
    assert fetch_strategy._write_strategy(
        data, str(tmp_path), workload=module.resolve_issue(data)) is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", [ValueError("too large"), urllib.error.URLError("offline")])
def test_initiative_download_failure_never_uses_description(tmp_path, failure):
    module = registry_module()
    data = issue()
    with patch("fetch_strategy.download_attachment", side_effect=failure):
        with pytest.raises(module.WorkloadError):
            fetch_strategy._write_strategy(
                data, str(tmp_path), "https://jira.example.com", "u", "t",
                workload=module.resolve_issue(data))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("labels", [[], ["strat-creator-rubric-pass"],
                                    ["strat-creator-human-sign-off"]])
def test_unapproved_initiative_fails_before_artifact_write(tmp_path, labels):
    module = registry_module()
    data = issue()
    data["fields"]["labels"] = labels
    with pytest.raises(module.WorkloadError):
        fetch_strategy._write_strategy(data, str(tmp_path), workload=module.resolve_issue(data))
    assert not list(tmp_path.iterdir())


def test_batch_does_not_skip_initiative_with_handmade_epics(tmp_path, monkeypatch):
    module = registry_module()
    monkeypatch.chdir(tmp_path)
    data = issue()
    data["fields"]["issuelinks"] = [{"type": {"name": "Incorporates"},
                                      "outwardIssue": {"key": "RHOAIENG-9002"}}]
    processed = issue("RHOAIENG-9003")
    processed["fields"]["labels"].append("epic-creator-auto-decomposed")
    missing = issue("RHOAIENG-9004")
    missing["fields"]["attachment"] = []
    with patch("fetch_strategy.require_env", return_value=("https://jira.example.com", "u", "t")), \
         patch("fetch_strategy._search_issues", return_value=[data, processed, missing]), \
         patch("fetch_strategy.download_attachment", return_value=b"# Approved strategy"), \
         patch("fetch_strategy.api_call_with_retry", side_effect=AssertionError("No child-Epic query for Initiatives")):
        fetch_strategy.cmd_fetch(["project = RHOAIENG", "--ids-file", "ids.txt",
                                  "--workload", "rhai-initiative", "--skip-if-has-epics"])
    assert Path("ids.txt").read_text() == "RHOAIENG-9001\n"
    assert not Path("artifacts/strat-tasks/RHOAIENG-9003.md").exists()
    assert not Path("artifacts/strat-tasks/RHOAIENG-9004.md").exists()


@pytest.mark.parametrize("failure", ["download", "timestamp", "frontmatter", "utf8", "empty"])
def test_batch_keeps_good_sources_around_a_bad_attachment(tmp_path, monkeypatch, capsys, failure):
    monkeypatch.chdir(tmp_path)
    bad = issue("RHOAIENG-9002")
    if failure == "timestamp":
        bad["fields"]["attachment"][0]["created"] = "not-a-timestamp"

    def download(url, user, token, server):
        if url.endswith("/bad"):
            if failure == "download":
                raise urllib.error.URLError("synthetic download failure")
            return {"frontmatter": b"---\nstrat_id: [\n---\n# Invalid",
                    "utf8": b"\xff", "empty": b""}[failure]
        return b"# Approved strategy"

    bad["fields"]["attachment"][0]["content"] = "https://jira.example.com/att/bad"
    with patch("fetch_strategy.require_env", return_value=("https://jira.example.com", "u", "t")), \
         patch("fetch_strategy._search_issues", return_value=[issue(), bad, issue("RHOAIENG-9003")]), \
         patch("fetch_strategy.download_attachment", side_effect=download):
        fetch_strategy.cmd_fetch(["project = RHOAIENG", "--ids-file", "ids.txt",
                                  "--workload", "rhai-initiative"])

    assert Path("ids.txt").read_text() == "RHOAIENG-9001\nRHOAIENG-9003\n"
    assert not Path("artifacts/strat-tasks/RHOAIENG-9002.md").exists()
    for key in ("RHOAIENG-9001", "RHOAIENG-9003"):
        metadata, body = artifact_utils.read_frontmatter_validated(
            f"artifacts/strat-tasks/{key}.md", "strat-task")
        assert metadata["source_key"] == key
        assert body.strip() == "# Approved strategy"
    stderr = capsys.readouterr().err
    assert "ERROR: Skipped RHOAIENG-9002:" in stderr
    assert "Skipped 1 strategies" in stderr
    assert "Fetched 2 strategies" in stderr


@pytest.mark.parametrize("created", [None, "not-a-timestamp", "2026-10-13T09:00:00"])
def test_fetch_one_rejects_invalid_attachment_timestamp(tmp_path, monkeypatch, created):
    monkeypatch.chdir(tmp_path)
    data = issue()
    data["fields"]["attachment"][0]["created"] = created
    with patch("fetch_strategy.require_env", return_value=("https://jira.example.com", "u", "t")), \
         patch("fetch_strategy.get_issue", return_value=data), \
         patch("fetch_strategy.download_attachment", side_effect=AssertionError("Invalid metadata")):
        with pytest.raises(registry_module().WorkloadError, match="invalid attachment timestamp"):
            fetch_strategy.cmd_fetch_one([data["key"], "--workload", "rhai-initiative"])
    assert not Path("artifacts").exists()


def test_legacy_artifact_has_no_added_workload_metadata(tmp_path):
    data = issue("RHAISTRAT-9001", "RHAISTRAT", "Feature")
    data["fields"]["attachment"] = []
    path = fetch_strategy._write_strategy(data, str(tmp_path))
    fm, body = artifact_utils.read_frontmatter_validated(path, "strat-task")
    assert body.strip() == "Initiative text, not strategy"
    assert "workload_id" not in fm


def test_refetch_replaces_old_strategy_body(tmp_path):
    module = registry_module()
    data = issue()
    with patch("fetch_strategy.download_attachment", side_effect=[b"# Old strategy", b"# New strategy"]):
        for _ in range(2):
            path = fetch_strategy._write_strategy(
                data, str(tmp_path), "https://jira.example.com", "u", "t",
                workload=module.resolve_issue(data))
    _, body = artifact_utils.read_frontmatter_validated(path, "strat-task")
    assert body.strip() == "# New strategy"


@pytest.mark.parametrize("body", [b"", b"\xff"])
def test_empty_or_invalid_utf8_initiative_attachment_is_rejected(tmp_path, body):
    module = registry_module()
    data = issue()
    with patch("fetch_strategy.download_attachment", return_value=body):
        with pytest.raises(module.WorkloadError):
            fetch_strategy._write_strategy(
                data, str(tmp_path), "https://jira.example.com", "u", "t",
                workload=module.resolve_issue(data))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("field,value", [
    ("strat_id", "RHOAIENG-9002"), ("jira_key", "RHOAIENG-9002"),
    ("source_initiative", "RHOAIENG-9002"), ("type", "rfe-strategy"),
])
def test_conflicting_upstream_attachment_identity_is_rejected(tmp_path, field, value):
    module = registry_module()
    data = issue()
    body = f"---\n{field}: {value}\n---\n# Conflicting strategy".encode()
    with patch("fetch_strategy.download_attachment", return_value=body):
        with pytest.raises(module.WorkloadError):
            fetch_strategy._write_strategy(
                data, str(tmp_path), "https://jira.example.com", "u", "t",
                workload=module.resolve_issue(data))
    assert not list(tmp_path.iterdir())


def test_fetch_one_selects_from_the_fetched_tracker_binding(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("fetch_strategy.require_env", return_value=("https://jira.example.com", "u", "t")), \
         patch("fetch_strategy.get_issue", return_value=issue()), \
         patch("fetch_strategy.download_attachment", return_value=b"# Approved strategy"):
        fetch_strategy.cmd_fetch_one(["RHOAIENG-9001", "--workload", "rhai-initiative"])
    data, _ = artifact_utils.read_frontmatter_validated(
        "artifacts/strat-tasks/RHOAIENG-9001.md", "strat-task")
    assert data["source_key"] == "RHOAIENG-9001"


def test_rfe_handoff_conflict_cannot_fall_back_to_description(tmp_path):
    module = registry_module()
    data = issue("RHAISTRAT-9001", "RHAISTRAT", "Feature")
    with patch("fetch_strategy.download_attachment", return_value=b"---\nstrat_id: RHAISTRAT-9002\n---\n# Wrong strategy"):
        with pytest.raises(module.WorkloadError):
            fetch_strategy._write_strategy(
                data, str(tmp_path), "https://jira.example.com", "u", "t",
                workload=module.resolve_issue(data))
    assert not list(tmp_path.iterdir())


def test_malformed_attachment_frontmatter_fails_with_contract_error(tmp_path):
    module = registry_module()
    data = issue()
    with patch("fetch_strategy.download_attachment", return_value=b"---\nstrat_id: [unterminated\n---\n# Strategy"):
        with pytest.raises(module.WorkloadError):
            fetch_strategy._write_strategy(
                data, str(tmp_path), "https://jira.example.com", "u", "t",
                workload=module.resolve_issue(data))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("body", [
    b"---\n- strat_id: RHOAIENG-9002\n---\n# Strategy",
    b"---\nstrat_id: RHOAIENG-9002\n# Missing closing delimiter",
    b"---\nstrat_id: RHOAIENG-9002\n---",
    b"---\nstrat_id: RHOAIENG-9001\n---",
])
def test_invalid_frontmatter_is_not_treated_as_absent(tmp_path, body):
    module = registry_module()
    data = issue()
    with patch("fetch_strategy.download_attachment", return_value=body):
        with pytest.raises(module.WorkloadError):
            fetch_strategy._write_strategy(
                data, str(tmp_path), "https://jira.example.com", "u", "t",
                workload=module.resolve_issue(data))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("field,value", [("project", None), ("issuetype", None)])
def test_malformed_tracker_binding_fails_with_contract_error(field, value):
    module = registry_module()
    data = issue()
    data["fields"][field] = value
    with pytest.raises(module.WorkloadError):
        module.resolve_issue(data)


@pytest.mark.parametrize("field", ["id", "content", "created"])
def test_attachment_missing_revision_metadata_fails_before_write(tmp_path, field):
    module = registry_module()
    data = issue()
    del data["fields"]["attachment"][0][field]
    with patch("fetch_strategy.download_attachment", return_value=b"# Strategy"):
        with pytest.raises(module.WorkloadError):
            fetch_strategy._write_strategy(
                data, str(tmp_path), "https://jira.example.com", "u", "t",
                workload=module.resolve_issue(data))
    assert not list(tmp_path.iterdir())


def typed_frontmatter(schema_type):
    data = {"workload_id": "rhai-initiative", "workload_version": 1,
            "workload_digest": registry_module().get_workload("rhai-initiative").digest,
            "source_key": "RHOAIENG-9001", "source_project": "RHOAIENG",
            "source_issue_type": "Initiative", "source_revision": "2026-10-13T10:00:00Z"}
    if schema_type == "strat-task":
        data.update(strat_id="RHOAIENG-9001", title="Synthetic strategy")
    elif schema_type == "epic-task":
        data.update(parent_strat="RHOAIENG-9001", epic_id="RHOAIENG-9001-E001",
                    title="Synthetic Epic", component="example", team="example",
                    type="Implementation", priority="P1")
    elif schema_type == "decomp-summary":
        data.update(parent_strat="RHOAIENG-9001", epic_count=1, critical_path_length=1)
    else:
        data.update(strat_id="RHOAIENG-9001", score=90, **{"pass": True}, recommendation="accept")
    return data


@pytest.mark.parametrize("schema_type", ["strat-task", "epic-task", "decomp-summary", "decomp-review"])
def test_all_artifact_schemas_round_trip_initiative_identity(tmp_path, schema_type):
    path = str(tmp_path / f"{schema_type}.md")
    data = typed_frontmatter(schema_type)
    artifact_utils.write_frontmatter(path, data, schema_type)
    loaded, _ = artifact_utils.read_frontmatter_validated(path, schema_type)
    assert loaded["source_key"] == "RHOAIENG-9001"
    assert loaded["workload_id"] == "rhai-initiative"


@pytest.mark.parametrize("field,value", [
    ("workload_id", "unknown"), ("workload_version", 2),
    ("source_project", "OTHER"), ("source_issue_type", "Epic"),
    ("source_key", "RHOAIENG-9002"), ("source_revision", ""),
])
def test_artifact_rejects_conflicting_or_incomplete_provenance(field, value):
    data = typed_frontmatter("strat-task")
    data[field] = value
    assert artifact_utils.validate(data, "strat-task")


def test_initiative_without_workload_identity_remains_invalid():
    data = {"strat_id": "RHOAIENG-9001", "title": "Unidentified strategy"}
    assert artifact_utils.validate(data, "strat-task")


def test_descriptor_change_invalidates_existing_artifact(tmp_path, monkeypatch):
    module = registry_module()
    data = typed_frontmatter("strat-task")
    assert not artifact_utils.validate(data, "strat-task")
    destination = tmp_path / "workloads"
    shutil.copytree(module.WORKLOADS_DIR, destination)
    monkeypatch.setattr(module, "WORKLOADS_DIR", destination)
    path = destination / "rhai-initiative.yaml"
    changed = yaml.safe_load(path.read_text())
    changed["source"]["ready_labels"].append("additional-approval")
    path.write_text(yaml.safe_dump(changed))
    errors = artifact_utils.validate(data, "strat-task")
    assert any("digest" in error for error in errors)
