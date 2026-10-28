"""Descriptor submission against a synthetic Jira HTTP boundary."""
from copy import deepcopy
from email.parser import BytesParser
from email.policy import default
import io
import json
from pathlib import Path
import re
import shutil
import socket
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import artifact_utils as artifacts
import submit
import workload
import workload_submit


class Response(io.BytesIO):
    status = 200
    headers = {}


class Jira:
    """Persist synthetic issues across reruns; fail before/after an HTTP effect."""

    def __init__(self):
        self.issues = {}
        self.links = set()
        self.writes = []
        self.attempts = []
        self.failure = None
        self.hidden = False
        self.incomplete = False
        self.next_key = 1

    def urlopen(self, request, **kwargs):
        path = urlparse(request.full_url).path.removeprefix("/rest/api/3")
        method = request.get_method()
        operation = (method, path)
        self.attempts.append(operation)
        if self.failure == (operation, "before"):
            self.failure = None
            raise URLError("synthetic transport failure")
        result = self.handle(method, path, request)
        if method != "GET" and path != "/search/jql":
            self.writes.append(operation)
        if self.failure == (operation, "after"):
            self.failure = None
            raise URLError("synthetic response lost after commit")
        return Response(json.dumps(result).encode())

    def handle(self, method, path, request):
        if method == "GET" and path.startswith("/issue/"):
            return deepcopy(self.issues[path.split("/")[2]])
        if path == "/search/jql":
            body = json.loads(request.data)
            marker = re.search(r'labels = "([^"]+)"', body["jql"]).group(1)
            issues = [deepcopy(i) for i in self.issues.values()
                      if marker in i["fields"].get("labels", [])]
            result = {"issues": [] if self.hidden else issues, "isLast": True}
            if self.incomplete:
                result.update(isLast=False, nextPageToken="more")
            return result
        if method == "POST" and path == "/issue":
            fields = json.loads(request.data)["fields"]
            key = f"{fields['project']['key']}-{self.next_key}"
            self.next_key += 1
            fields.update(attachment=[], issuelinks=[])
            self.issues[key] = {"key": key, "id": str(self.next_key), "fields": fields}
            return {"key": key, "id": str(self.next_key)}
        if method == "PUT" and path.startswith("/issue/"):
            issue = self.issues[path.split("/")[2]]
            body = json.loads(request.data)
            issue["fields"].update(body.get("fields", {}))
            for label in body.get("update", {}).get("labels", []):
                if label["add"] not in issue["fields"]["labels"]:
                    issue["fields"]["labels"].append(label["add"])
            return None
        if method == "POST" and path == "/issueLink":
            body = json.loads(request.data)
            inward, outward = body["inwardIssue"]["key"], body["outwardIssue"]["key"]
            name = body["type"]["name"]
            self.links.add((name, inward, outward))
            self.issues[inward]["fields"]["issuelinks"].append(
                {"type": {"name": name}, "outwardIssue": {"key": outward}})
            self.issues[outward]["fields"]["issuelinks"].append(
                {"type": {"name": name}, "inwardIssue": {"key": inward}})
            return None
        if method == "POST" and path.endswith("/attachments"):
            key = path.split("/")[2]
            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {request.get_header('Content-type')}\r\n\r\n".encode()
                + request.data)
            part = next(message.iter_parts())
            attachment = {"id": str(len(self.writes) + 1),
                          "filename": part.get_filename(),
                          "content": "https://jira.example.test/attachment/content/1"}
            self.issues[key]["fields"]["attachment"].append(attachment)
            return [attachment]
        raise AssertionError(f"Unexpected HTTP request: {method} {path}")


@pytest.fixture
def case(tmp_path, monkeypatch):
    registry = tmp_path / "repo/workloads"
    shutil.copytree(ROOT / "workloads", registry)
    components = registry.parent / ".context/rhai-components.txt"
    components.parent.mkdir()
    components.write_text("test-component\n")
    monkeypatch.setattr(workload, "WORKLOADS_DIR", registry)
    monkeypatch.chdir(tmp_path)
    for name, value in {"JIRA_SERVER": "https://jira.example.test", "JIRA_USER": "synthetic",
                        "JIRA_TOKEN": "synthetic"}.items():
        monkeypatch.setenv(name, value)
    jira = Jira()
    monkeypatch.setattr("urllib.request.urlopen", jira.urlopen)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("unexpected real network"))
    return registry, jira


def setup(case, key="RHOAIENG-9001", artifacts_dir="artifacts", workload_id="rhai-initiative"):
    registry, jira = case
    selected = workload.get_workload(workload_id)
    metadata = dict(workload_id=selected.id, workload_version=selected.version,
                    workload_digest=selected.digest, planning_digest=selected.planning.digest,
                    source_key=key, source_project=selected.project,
                    source_issue_type=selected.issue_type, source_revision="synthetic-revision",
                    attachment_id="9001", attachment_created="2026-10-13T09:00:00Z")
    root = Path(artifacts_dir)
    artifacts.write_frontmatter(root / f"strat-tasks/{key}.md",
        dict(metadata, strat_id=key, title="Synthetic strategy", labels=list(selected.ready_labels)),
        "strat-task", body="Synthetic strategy\n")
    artifacts.write_frontmatter(root / f"epic-tasks/{key}-decomposition.md",
        dict(metadata, parent_strat=key, epic_count=2, critical_path_length=2),
        "decomp-summary", body="Synthetic plan\n")
    artifacts.write_frontmatter(root / f"epic-reviews/{key}-decomp-review.md",
        dict(metadata, strat_id=key, score=14, criterion_scores=[2]*7,
             recommendation="accept", issues=[], **{"pass": True}), "decomp-review")
    for number in (1, 2):
        epic = f"{key}-E{number:03}"
        artifacts.write_frontmatter(root / f"epic-tasks/{epic}.md",
            dict(metadata, epic_id=epic, parent_strat=key, title=f"Synthetic epic {number}",
                 type="Investigation" if number == 1 else "Implementation",
                 component="test-component", team="Synthetic", priority="P1",
                 dependencies=[] if number == 1 else [f"{key}-E001"]),
            "epic-task", body=f"## Description\n\nSynthetic work {number}\n")
    jira.issues[key] = {"key": key, "id": "9001", "fields": {
        "project": {"key": selected.project}, "issuetype": {"name": selected.issue_type},
        "labels": list(selected.ready_labels), "assignee": {"accountId": "synthetic-account"},
        "issuelinks": [], "attachment": [{"id": "9001", "filename": f"{key}-strategy.md",
            "created": "2026-10-13T09:00:00Z", "content": "https://jira.example.test/attachment/9001"}]}}
    return root


def run(monkeypatch, *args, workload_id="rhai-initiative"):
    monkeypatch.setattr(sys, "argv", ["submit.py", "--workload", workload_id, *args])
    try:
        submit.main()
        return 0
    except SystemExit as exc:
        return exc.code


def test_initiative_submission_creates_original_parent_and_provenance(case, monkeypatch):
    setup(case)
    _, jira = case
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    assert set(jira.issues) == {"RHOAIENG-9001", "RHAI-1", "RHAI-2"}
    first = jira.issues["RHAI-1"]["fields"]
    assert first["parent"] == {"key": "RHOAIENG-9001"}
    assert first["issuetype"] == {"name": "Epic"}
    assert first["components"] == [{"name": "test-component"}]
    assert "epic-creator-investigation" in first["labels"]
    assert first["assignee"] == {"accountId": "synthetic-account"}
    assert ("Blocks", "RHAI-1", "RHAI-2") in jira.links
    assert "epic-creator-auto-decomposed" in jira.issues["RHOAIENG-9001"]["fields"]["labels"]
    data, _ = artifacts.read_frontmatter("artifacts/epic-tasks/RHOAIENG-9001-E001.md")
    assert data["jira_key"] == "RHAI-1"
    assert data["source_key"] == "RHOAIENG-9001"
    assert any(a["filename"].startswith("RHAI-1-frontmatter-") for a in first["attachment"])


def test_rerun_has_no_duplicate_issues_links_or_attachments(case, monkeypatch):
    setup(case)
    _, jira = case
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    before = deepcopy(jira.issues), set(jira.links), list(jira.writes)
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    assert (jira.issues, jira.links, jira.writes) == before


@pytest.mark.parametrize("branch", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_typed_submission_rejects_unknown_component_before_writes(case, monkeypatch, capsys, branch, dry_run):
    root = setup(case)
    _, jira = case
    target = add_branch(root) if branch else root / "epic-tasks/RHOAIENG-9001-E002.md"
    artifacts.update_frontmatter(target,
                                {"component": "unregistered-component"}, "epic-task")
    args = ["--dry-run"] if dry_run else []
    assert run(monkeypatch, *args, "RHOAIENG-9001") == 1
    assert jira.writes == []
    assert "component" in capsys.readouterr().err


@pytest.mark.parametrize("empty", [False, True])
def test_typed_dry_run_requires_component_cache(case, monkeypatch, capsys, empty):
    setup(case)
    registry, jira = case
    cache = registry.parent / ".context/rhai-components.txt"
    if empty:
        cache.write_text("\n")
    else:
        cache.unlink()
    assert run(monkeypatch, "--dry-run", "RHOAIENG-9001") == 1
    assert jira.writes == []
    assert "fetch_components" in capsys.readouterr().err


def test_submission_uses_selected_destination_labels_and_link_policy(case, monkeypatch):
    registry, jira = case
    path = registry / "rhai-initiative.yaml"
    data = yaml.safe_load(path.read_text())
    data["planning"]["components"]["project"] = "OTHER"
    data["submission"]["project"] = "OTHER"
    data["submission"]["issue_type"] = "Task"
    data["submission"]["parent"] = {"relation": "link", "link_type": "Relates"}
    data["submission"]["dependency_link_type"] = "Requires"
    data["submission"]["labels"]["created"] = "synthetic-created"
    data["source"]["processed_label"] = "synthetic-processed"
    path.write_text(yaml.safe_dump(data))
    setup(case)
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    fields = jira.issues["OTHER-1"]["fields"]
    assert fields["issuetype"] == {"name": "Task"}
    assert "parent" not in fields
    assert "synthetic-created" in fields["labels"]
    assert ("Relates", "RHOAIENG-9001", "OTHER-1") in jira.links
    assert ("Requires", "OTHER-1", "OTHER-2") in jira.links
    assert "synthetic-processed" in jira.issues["RHOAIENG-9001"]["fields"]["labels"]


@pytest.mark.parametrize("broken", ["missing_epic", "invalid_epic", "mixed_provenance", "failed_review",
    "unknown_dependency", "cycle", "missing_source", "missing_approval", "missing_branch_gate"])
def test_preflight_rejects_whole_artifact_set_before_any_write(case, monkeypatch, broken):
    root = setup(case)
    _, jira = case
    epic = root / "epic-tasks/RHOAIENG-9001-E002.md"
    review = root / "epic-reviews/RHOAIENG-9001-decomp-review.md"
    if broken == "missing_epic":
        epic.unlink()
    elif broken == "invalid_epic":
        epic.write_text("Missing metadata\n")
    elif broken == "mixed_provenance":
        artifacts.update_frontmatter(epic, {"source_revision": "different"}, "epic-task")
    elif broken == "failed_review":
        artifacts.update_frontmatter(review, {"score": 7, "criterion_scores": [1]*7,
            "pass": False, "recommendation": "revise"}, "decomp-review")
    elif broken == "unknown_dependency":
        artifacts.update_frontmatter(epic, {"dependencies": ["RHOAIENG-9001-E099"]}, "epic-task")
    elif broken == "cycle":
        artifacts.update_frontmatter(root / "epic-tasks/RHOAIENG-9001-E001.md",
            {"dependencies": ["RHOAIENG-9001-E002"]}, "epic-task")
    elif broken == "missing_source":
        (root / "strat-tasks/RHOAIENG-9001.md").unlink()
    elif broken == "missing_approval":
        jira.issues["RHOAIENG-9001"]["fields"]["labels"] = []
    else:
        data, body = artifacts.read_frontmatter(epic)
        data.update(epic_id="RHOAIENG-9001-BRANCH-A-E003", branch="A", gated_by="RHOAIENG-9001-E099")
        artifacts.write_frontmatter(root / "epic-tasks/RHOAIENG-9001-BRANCH-A-E003.md", data, "epic-task", body=body)
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert jira.writes == []


@pytest.mark.parametrize("operation,when", [(('POST', '/issue'), 'after'),
    (('POST', '/issueLink'), 'before'), (('POST', '/issueLink'), 'after'),
    (('POST', '/issue/RHAI-1/attachments'), 'before'),
    (('POST', '/issue/RHAI-1/attachments'), 'after'),
    (('PUT', '/issue/RHOAIENG-9001'), 'before')])
def test_rerun_recovers_each_failed_phase(case, monkeypatch, operation, when):
    setup(case)
    _, jira = case
    jira.failure = (operation, when)
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert "epic-creator-auto-decomposed" not in jira.issues["RHOAIENG-9001"]["fields"]["labels"]
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    assert len(jira.issues) == 3
    assert jira.links == {("Blocks", "RHAI-1", "RHAI-2")}
    for key in ("RHAI-1", "RHAI-2"):
        assert len(jira.issues[key]["fields"]["attachment"]) == 1


def test_recovers_created_issue_when_local_key_was_not_saved(case, monkeypatch):
    setup(case)
    _, jira = case
    original = artifacts.update_frontmatter
    def fail_once(path, updates, schema):
        raise OSError("synthetic journal write failure")
    monkeypatch.setattr(artifacts, "update_frontmatter", fail_once)
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert len(jira.issues) == 2
    monkeypatch.setattr(artifacts, "update_frontmatter", original)
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    assert len(jira.issues) == 3


def test_uncertain_create_and_stale_search_never_recreates(case, monkeypatch):
    setup(case)
    _, jira = case
    jira.failure = (("POST", "/issue"), "after")
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    jira.hidden = True
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert len(jira.issues) == 2
    assert jira.attempts.count(("POST", "/issue")) == 1
    jira.hidden = False
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    assert len(jira.issues) == 3


@pytest.mark.parametrize(("status_code", "record_expected"), [(400, False), (503, True)])
def test_create_http_error_clears_only_definite_rejection(
        case, monkeypatch, status_code, record_expected):
    setup(case)
    _, jira = case

    def reject_create(*args, **kwargs):
        raise HTTPError("https://jira.example.test/rest/api/3/issue",
                        status_code, "synthetic Jira response", {}, None)

    monkeypatch.setattr(workload_submit.jira, "create_issue", reject_create)
    assert run(monkeypatch, "RHOAIENG-9001") == 1

    journal = json.loads(Path("artifacts/submissions/rhai-initiative/RHOAIENG-9001.json").read_text())
    epic_record = journal["epics"].get("RHOAIENG-9001-E001")
    assert (epic_record is not None) is record_expected
    if record_expected:
        assert epic_record["attempted"] is True
        assert epic_record["jira_key"] is None
    assert jira.writes == []


@pytest.mark.parametrize("corrupt_record", [None, {}])
def test_invalid_journal_record_cannot_erase_a_pending_create(case, monkeypatch, corrupt_record):
    setup(case)
    _, jira = case
    jira.failure = (("POST", "/issue"), "after")
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    path = Path("artifacts/submissions/rhai-initiative/RHOAIENG-9001.json")
    data = json.loads(path.read_text())
    data["epics"]["RHOAIENG-9001-E001"] = corrupt_record
    path.write_text(json.dumps(data))
    jira.hidden = True
    before = list(jira.writes)
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert jira.writes == before


def test_duplicate_recovery_matches_stop_without_writes(case, monkeypatch):
    setup(case)
    _, jira = case
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    jira.issues["RHAI-99"] = deepcopy(jira.issues["RHAI-1"])
    jira.issues["RHAI-99"]["key"] = "RHAI-99"
    before = len(jira.writes)
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert len(jira.writes) == before


def test_incomplete_recovery_search_is_not_treated_as_no_match(case, monkeypatch):
    setup(case)
    _, jira = case
    jira.incomplete = True
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert jira.writes == []


def test_dry_run_is_local_only_and_accepts_custom_artifacts_dir(case, monkeypatch, capsys):
    setup(case, artifacts_dir="custom")
    _, jira = case
    assert run(monkeypatch, "--dry-run", "--artifacts-dir", "custom", "--all") == 0
    output = capsys.readouterr().out
    assert "RHOAIENG-9001-E001" in output
    assert "parent: RHOAIENG-9001" in output
    assert "Blocks" in output
    assert "epic-creator-auto-created" in output
    assert jira.attempts == []
    assert not Path("custom/submissions").exists()


def test_typed_artifacts_cannot_silently_use_legacy_submission(case, monkeypatch):
    setup(case)
    _, jira = case
    monkeypatch.setattr(sys, "argv", ["submit.py", "RHOAIENG-9001"])
    with pytest.raises(SystemExit) as exc:
        submit.main()
    assert exc.value.code == 1
    assert jira.writes == []


def add_branch(root):
    data, _ = artifacts.read_frontmatter(root / "epic-tasks/RHOAIENG-9001-E002.md")
    data.update(epic_id="RHOAIENG-9001-BRANCH-A-E003", branch="A",
                gated_by="RHOAIENG-9001-E001", dependencies=["RHOAIENG-9001-E001"])
    path = root / "epic-tasks/RHOAIENG-9001-BRANCH-A-E003.md"
    artifacts.write_frontmatter(path, data, "epic-task", body="Synthetic conditional work\n")
    return path


def test_conditional_epics_are_attached_to_gate_without_creating_issues(case, monkeypatch):
    root = setup(case)
    add_branch(root)
    _, jira = case
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    assert len(jira.issues) == 3
    assert len(jira.issues["RHAI-1"]["fields"]["attachment"]) == 2
    assert any("branch-a-plan-" in a["filename"] for a in jira.issues["RHAI-1"]["fields"]["attachment"])
    before = list(jira.writes)
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    assert jira.writes == before


def test_recovery_identity_survives_moving_the_artifact_directory(case, monkeypatch):
    root = setup(case)
    add_branch(root)
    _, jira = case
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    shutil.copytree(root, "moved")
    before = list(jira.writes)
    assert run(monkeypatch, "--artifacts-dir", "moved", "RHOAIENG-9001") == 0
    assert jira.writes == before


@pytest.mark.parametrize("change_kind", ["body", "branch", "team"])
def test_changed_reviewed_content_cannot_reuse_an_existing_epic(case, monkeypatch, change_kind):
    root = setup(case)
    branch = add_branch(root)
    _, jira = case
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    epic = root / "epic-tasks/RHOAIENG-9001-E001.md"
    if change_kind == "team":
        artifacts.update_frontmatter(epic, {"team": "Different"}, "epic-task")
    else:
        path = branch if change_kind == "branch" else epic
        with path.open("a") as stream:
            stream.write("Different reviewed content\n")
    before = list(jira.writes)
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert jira.writes == before


def test_typed_legacy_workload_has_explicit_submission_path(case, monkeypatch):
    setup(case, key="RHAISTRAT-9001", workload_id="rhaistrat-strategy")
    _, jira = case
    assert run(monkeypatch, "RHAISTRAT-9001", workload_id="rhaistrat-strategy") == 0
    assert jira.issues["RHAI-1"]["fields"]["parent"] == {"key": "RHAISTRAT-9001"}


def description_source(case):
    root = setup(case, key="RHAISTRAT-9001", workload_id="rhaistrat-strategy")
    for path in root.glob("*/*.md"):
        schema = ("strat-task" if path.parent.name == "strat-tasks" else
                  "decomp-review" if path.parent.name == "epic-reviews" else
                  "decomp-summary" if path.name.endswith("-decomposition.md") else "epic-task")
        data, body = artifacts.read_frontmatter(path)
        data.pop("attachment_id")
        data.pop("attachment_created")
        artifacts.write_frontmatter(path, data, schema, body=body)
    fields = case[1].issues["RHAISTRAT-9001"]["fields"]
    fields.update(description="Synthetic strategy", updated="synthetic-revision", attachment=[])
    return fields


@pytest.mark.parametrize("superseded", ["description", "attachment"])
def test_superseded_description_strategy_cannot_be_submitted(case, monkeypatch, superseded):
    fields = description_source(case)
    if superseded == "description":
        fields.update(description="Changed strategy", updated="new-revision")
    else:
        fields["attachment"] = [{"id": "9002", "filename": "RHAISTRAT-9001-strategy.md",
                                  "created": "2026-10-14T09:00:00Z"}]
    assert run(monkeypatch, "RHAISTRAT-9001", workload_id="rhaistrat-strategy") == 1
    assert case[1].writes == []


def test_description_strategy_rerun_accepts_own_label_revision(case, monkeypatch):
    fields = description_source(case)
    assert run(monkeypatch, "RHAISTRAT-9001", workload_id="rhaistrat-strategy") == 0
    fields["updated"] = "updated-by-submission-label"
    before = list(case[1].writes)
    assert run(monkeypatch, "RHAISTRAT-9001", workload_id="rhaistrat-strategy") == 0
    assert case[1].writes == before


@pytest.mark.parametrize("conflict", ["project", "type", "parent", "identity", "content", "local_key"])
def test_existing_issue_conflicts_fail_before_mutation(case, monkeypatch, conflict):
    root = setup(case)
    _, jira = case
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    fields = jira.issues["RHAI-1"]["fields"]
    if conflict == "project":
        fields["project"]["key"] = "OTHER"
    elif conflict == "type":
        fields["issuetype"]["name"] = "Bug"
    elif conflict == "parent":
        fields["parent"]["key"] = "RHOAIENG-9002"
    elif conflict in ("identity", "content"):
        prefix = "epic-creator-id-" if conflict == "identity" else "epic-creator-plan-"
        fields["labels"] = [v for v in fields["labels"] if not v.startswith(prefix)]
    else:
        artifacts.update_frontmatter(root / "epic-tasks/RHOAIENG-9001-E001.md", {"jira_key": "RHAI-99"}, "epic-task")
    before = list(jira.writes)
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert jira.writes == before


def test_missing_parent_and_labels_are_reconciled(case, monkeypatch):
    setup(case)
    _, jira = case
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    fields = jira.issues["RHAI-1"]["fields"]
    fields.pop("parent")
    fields["labels"].remove("epic-creator-investigation")
    assert run(monkeypatch, "RHOAIENG-9001") == 0
    assert fields["parent"] == {"key": "RHOAIENG-9001"}
    assert "epic-creator-investigation" in fields["labels"]
    assert len(jira.issues) == 3


def test_unapproved_newer_strategy_attachment_blocks_submission(case, monkeypatch):
    setup(case)
    _, jira = case
    jira.issues["RHOAIENG-9001"]["fields"]["attachment"].append({
        "id": "9002", "filename": "RHOAIENG-9001-strategy.md", "created": "2026-10-14T09:00:00Z"})
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert jira.writes == []


def test_local_lock_prevents_a_second_submitter(case, monkeypatch):
    import fcntl
    setup(case)
    _, jira = case
    path = Path("artifacts/submissions/rhai-initiative/RHOAIENG-9001.lock")
    path.parent.mkdir(parents=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert jira.writes == []


def test_invalid_submission_descriptor_fails_without_writes(case, monkeypatch, capsys):
    setup(case)
    registry, jira = case
    path = registry / "rhai-initiative.yaml"
    data = yaml.safe_load(path.read_text())
    data["submission"]["parent"] = {"relation": "link"}
    path.write_text(yaml.safe_dump(data))
    assert run(monkeypatch, "RHOAIENG-9001") == 1
    assert "link_type" in capsys.readouterr().err
    assert jira.writes == []
