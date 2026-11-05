"""Repository publication through the real submission code and a fake Jira transport."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from repository_fixtures import git, proposal, write_repository_decomposition, write_artifact
import artifact_utils as artifacts
import repository_context
import repository_plan
import submit
import repository_submit
from test_repository_pipeline import setup_pipeline
from jira_transport import Jira


@pytest.fixture
def publication(monkeypatch, tmp_path, capsys, request):
    selected, source = setup_pipeline(monkeypatch, tmp_path, capsys)
    if getattr(request, "param", False):
        policy = selected.repository_policy
        repo = Path(policy["sources"][0]["url"].removeprefix("file://"))
        (repo / "planning.md").write_text("Read mapping.md for ownership; architecture.json explains component boundaries.\n")
        git(repo, "add", ".")
        git(repo, "commit", "--signoff", "-m", "Add planning guidance")
        policy["sources"][0]["paths"].append("planning.md")
        policy["guidance"] = {"source_id": "mapping", "path": "planning.md"}
    root = Path("artifacts")
    context = repository_context.load_repository_context(selected, source, root)
    plan = repository_plan.finalize_repository_plan(context, proposal(context))
    locations = repository_plan.paths(root, source.stem)
    repository_plan.atomic_json(locations["context"], context)
    repository_plan.atomic_json(locations["plan"], plan)
    metadata = write_repository_decomposition(source, plan)
    write_artifact(root / f"epic-reviews/{source.stem}-decomp-review.md",
        {**metadata, "strat_id": source.stem, "score": 14, "pass": True,
         "recommendation": "accept",
         "repository_plan_digest": plan["digest"],
         "reviewed_decomposition_digest": repository_plan.decomposition_digest(plan, root, source.stem)},
        "decomp-review", body="Reviewed repository scope and acceptance coverage\n")
    jira = Jira()
    jira.issues[source.stem] = {"key": source.stem, "fields": {
        "project": {"key": "RHAISTRAT"}, "issuetype": {"name": "Feature"},
        "labels": ["strat-creator-rubric-pass", "strat-creator-human-sign-off"], "assignee": None, "issuelinks": [],
        "description": artifacts.read_frontmatter(source)[1].strip(), "attachment": []}}
    monkeypatch.setattr("urllib.request.urlopen", jira.urlopen)
    monkeypatch.setattr("time.sleep", lambda _: None)
    for name, value in {"JIRA_SERVER": "https://jira.example.test", "JIRA_USER": "synthetic", "JIRA_TOKEN": "synthetic"}.items():
        monkeypatch.setenv(name, value)
    return selected, source, context, plan, jira


def run(monkeypatch, source, *, explicit=True):
    monkeypatch.setattr(sys, "argv", ["submit.py", source.stem])
    try:
        submit.main()
    except SystemExit as result:
        return result.code
    return 0


def test_shared_epic_creator_preserves_repository_scope(publication):
    selected, source, _, _, jira = publication
    plan = repository_submit.preflight(selected, source.stem, Path("artifacts"))["plan"]
    keys, errors = submit._create_epics("https://jira.example.test", "synthetic", "synthetic", plan, source.stem)
    assert errors == 0
    fields = jira.issues[keys[f"{source.stem}-E001"]]["fields"]
    assert fields["components"] == [{"name": "Operator"}, {"name": "Cloudmanager"}]
    assert "https://github.com/example/operator" in json.dumps(fields["description"])
    assert "target-repo-operator" in fields["labels"]


def test_empty_repository_batch_is_successful(publication, monkeypatch):
    _, _, _, _, jira = publication
    Path("artifacts/epic-tasks").rename("artifacts/prepared-epics")
    monkeypatch.setattr(sys, "argv", ["submit.py", "--all"])
    try:
        submit.main()
    except SystemExit as result:
        assert result.code == 0
    assert jira.writes == []


def test_initial_create_contains_matching_target_and_all_components(publication, monkeypatch):
    _, source, context, _, jira = publication
    assert run(monkeypatch, source) == 0
    fields = jira.issues["RHAI-1"]["fields"]
    assert [label for label in fields["labels"] if label.startswith("target-repo-")] == ["target-repo-operator"]
    assert fields["components"] == [{"name": "Operator"}, {"name": "Cloudmanager"}]
    assert "epic-creator-auto-created" in fields["labels"]
    assert "https://github.com/example/operator" in json.dumps(fields["description"])
    assert context["digest"] in json.dumps(fields["description"])
    assert any("repository-evidence-" in a["filename"] for a in fields["attachment"])
    before = deepcopy(jira.issues), list(jira.writes)
    assert run(monkeypatch, source) == 0
    assert (jira.issues, jira.writes) == before


@pytest.mark.parametrize("change", ["missing", "target", "body", "mapping", "unresolved", "review", "expired"])
def test_invalid_repository_submission_has_zero_writes(publication, monkeypatch, change):
    _, source, context, plan, jira = publication
    path = Path(f"artifacts/epic-tasks/{source.stem}-E001.md")
    if change == "missing":
        data, body = artifacts.read_frontmatter(path)
        data.pop("target_repository")
        # Deliberately bypass the producer's frontmatter validation.
        import yaml
        path.write_text("---\n" + yaml.safe_dump(data) + "---\n" + body)
    elif change == "target":
        data, _ = artifacts.read_frontmatter(path)
        artifacts.update_frontmatter(path, {"target_repository": {**data["target_repository"], "repository": "dashboard"}}, "epic-task")
    elif change == "body": path.write_text(path.read_text() + "Modify dashboard source too.\n")
    elif change == "mapping":
        repo = Path(context["policy"]["sources"][0]["url"].removeprefix("file://"))
        (repo / "mapping.md").write_text("Changed ownership\n")
        git(repo, "add", ".")
        git(repo, "commit", "--signoff", "-m", "Changed mapping")
    elif change == "unresolved":
        plan["ready_for_drafting"] = False
        repository_plan.atomic_json(repository_plan.paths("artifacts", source.stem)["plan"], plan)
    elif change == "expired":
        context["sources"][0]["max_age_seconds"] = 1
        context["sources"][0]["fetched_at"] = "2020-01-01T00:00:00+00:00"
        # Rebind so the failure exercises freshness, not tamper detection.
        context["digest"] = repository_context.digest(repository_context._binding(context))
        repository_plan.atomic_json(repository_plan.paths("artifacts", source.stem)["context"], context)
    else:
        review = Path(f"artifacts/epic-reviews/{source.stem}-decomp-review.md")
        artifacts.update_frontmatter(review, {"repository_plan_digest": "0" * 64}, "decomp-review")
    assert run(monkeypatch, source) == 1
    assert jira.writes == []


@pytest.mark.parametrize("labels", [[], ["target-repo-dashboard"], ["target-repo-operator", "target-repo-dashboard"]])
def test_recovery_rejects_changed_repository_without_relabel_or_create(publication, monkeypatch, labels):
    _, source, _, _, jira = publication
    assert run(monkeypatch, source) == 0
    fields = jira.issues["RHAI-1"]["fields"]
    fields["labels"] = [v for v in fields["labels"] if not v.startswith("target-repo-")] + labels + ["keep-me"]
    before = deepcopy(jira.issues), list(jira.writes)
    assert run(monkeypatch, source) == 1
    assert (jira.issues, jira.writes) == before


def test_recovery_rejects_changed_published_scope(publication, monkeypatch):
    _, source, _, _, jira = publication
    assert run(monkeypatch, source) == 0
    jira.issues["RHAI-1"]["fields"]["description"] = {"type": "doc", "version": 1, "content": []}
    before = list(jira.writes)
    assert run(monkeypatch, source) == 1
    assert jira.writes == before


def test_lost_create_response_recovers_same_target(publication, monkeypatch):
    _, source, _, _, jira = publication
    jira.failure = (("POST", "/issue"), "after")
    assert run(monkeypatch, source) == 1
    assert "RHAI-1" in jira.issues
    jira.failure = None
    jira.issues["RHAI-1"]["fields"]["labels"].append("keep-me")
    assert run(monkeypatch, source) == 0
    assert set(jira.issues) == {source.stem, "RHAI-1"}
    assert "keep-me" in jira.issues["RHAI-1"]["fields"]["labels"]


@pytest.mark.parametrize("search", ["hidden", "incomplete"])
def test_uncertain_create_is_not_repeated_without_a_complete_match(publication, monkeypatch, search):
    _, source, _, _, jira = publication
    jira.failure = (("POST", "/issue"), "after")
    assert run(monkeypatch, source) == 1
    setattr(jira, search, True)
    before = list(jira.writes)
    assert run(monkeypatch, source) == 1
    assert jira.writes == before
    assert set(jira.issues) == {source.stem, "RHAI-1"}


def test_failed_evidence_upload_does_not_mark_strategy_complete(publication, monkeypatch):
    _, source, _, _, jira = publication
    jira.failure = (("POST", "/issue/RHAI-1/attachments"), "after")
    assert run(monkeypatch, source) == 1
    assert "epic-creator-auto-decomposed" not in jira.issues[source.stem]["fields"]["labels"]
    assert run(monkeypatch, source) == 0
    assert "epic-creator-auto-decomposed" in jira.issues[source.stem]["fields"]["labels"]
    assert set(jira.issues) == {source.stem, "RHAI-1"}


def test_conditional_repository_plan_is_attached_to_its_investigation(publication, monkeypatch):
    _, source, context, _, jira = publication
    draft = proposal(context)
    draft["work_items"][0]["kind"] = "investigation"
    draft["work_items"][0].pop("resolution")
    draft["work_items"][0].pop("planned_pr")
    draft["work_items"][1].update(branch="A", gated_by="W1", depends_on=["W1"])
    plan = repository_plan.finalize_repository_plan(context, draft)
    repository_plan.atomic_json(repository_plan.paths("artifacts", source.stem)["plan"], plan)
    metadata = write_repository_decomposition(source, plan)
    write_artifact(Path(f"artifacts/epic-reviews/{source.stem}-decomp-review.md"),
        {**metadata, "strat_id": source.stem, "score": 14, "pass": True, "recommendation": "accept",
         "repository_plan_digest": plan["digest"],
         "reviewed_decomposition_digest": repository_plan.decomposition_digest(plan, Path("artifacts"), source.stem)},
        "decomp-review", body="Reviewed conditional repository scope\n")
    assert run(monkeypatch, source) == 0
    assert set(jira.issues) == {source.stem, "RHAI-1"}
    fields = jira.issues["RHAI-1"]["fields"]
    assert "epic-creator-investigation" in fields["labels"]
    assert not any(label.startswith("target-repo-") for label in fields["labels"])
    assert any("branch-a-plan-" in attachment["filename"] for attachment in fields["attachment"])
    before = list(jira.writes)
    assert run(monkeypatch, source) == 0
    assert jira.writes == before


def test_evidence_export_contains_pinned_sources_without_cache_paths(publication):
    selected, source, context, _, _ = publication
    submission = repository_submit.preflight(selected, source.stem, Path("artifacts"))
    evidence = submission["plan"][0]["repository_evidence"]
    assert evidence["mapping_snapshot_digest"] == context["digest"]
    assert evidence["inventory_revision"] == "fixture-v1"
    assert evidence["sources"][0]["commit"] == context["sources"][0]["commit"]
    assert "local_path" not in json.dumps(evidence)
    assert "cache_root" not in json.dumps(evidence)


@pytest.mark.parametrize("publication", [True], indirect=True)
def test_evidence_export_identifies_bound_guidance(publication):
    selected, source, context, _, _ = publication
    evidence = repository_submit.preflight(selected, source.stem, Path("artifacts"))["plan"][0]["repository_evidence"]
    assert evidence.get("guidance") == {"source_id": "mapping", "path": "planning.md",
                                       "sha256": context["sources"][0]["files"]["planning.md"]["sha256"]}
    assert "local_path" not in json.dumps(evidence)


@pytest.mark.parametrize("publication", [True], indirect=True)
def test_guidance_change_after_review_prevents_publication(publication, monkeypatch):
    _, source, context, _, jira = publication
    repo = Path(context["policy"]["sources"][0]["url"].removeprefix("file://"))
    (repo / "planning.md").write_text("New interpretation instructions require replanning.\n")
    git(repo, "add", ".")
    git(repo, "commit", "--signoff", "-m", "Update planning guidance")
    assert run(monkeypatch, source) == 1
    assert jira.writes == []


def test_html_report_shows_repository_target(publication):
    from generate_html_report import render_epic_card, render_strategy_section
    _, source, _, _, _ = publication
    data, body = artifacts.read_frontmatter(f"artifacts/epic-tasks/{source.stem}-E001.md")
    html, _, _ = render_epic_card(data, body)
    assert "github.com/example/operator" in html
    assert data["mapping_snapshot_digest"] in html
    assert "Cloudmanager" in html
    html, _, _ = render_strategy_section(source.stem)
    assert "fixture-v1" in html


@pytest.fixture
def unresolved_publication(publication):
    selected, source, context, _, jira = publication
    draft = proposal(context)
    draft["work_items"][1]["resolution"] = {
        "status": "unresolved", "reason": "repository_unknown", "candidates": ["operator"]}
    plan = repository_plan.finalize_repository_plan(context, draft)
    repository_plan.atomic_json(repository_plan.paths("artifacts", source.stem)["plan"], plan)
    metadata = write_repository_decomposition(source, plan)
    write_artifact(Path(f"artifacts/epic-reviews/{source.stem}-decomp-review.md"),
        {**metadata, "strat_id": source.stem, "score": 14, "pass": True, "recommendation": "accept",
         "repository_plan_digest": plan["digest"],
         "reviewed_decomposition_digest": repository_plan.decomposition_digest(plan, Path("artifacts"), source.stem)},
        "decomp-review", body="Reviewed unresolved scope and warning\n")
    return selected, source, plan, jira


def test_unresolved_epic_is_created_with_warning_and_evidence(unresolved_publication, monkeypatch):
    selected, source, plan, jira = unresolved_publication
    submission = repository_submit.preflight(selected, source.stem, Path("artifacts"))
    entry = submission["plan"][0]
    assert entry["repository_evidence"]["inventory_entry"] is None
    assert entry["repository_evidence"]["work_items"][1]["resolution"]["candidates"] == ["operator"]
    assert run(monkeypatch, source) == 0
    fields = jira.issues["RHAI-1"]["fields"]
    warning = repository_plan.REPOSITORY_WARNING
    assert warning in json.dumps(fields["description"])
    assert fields["comment"] == [{"id": "1", "body": submit.markdown_to_adf(warning)}]
    assert not any(label.startswith("target-repo-") for label in fields["labels"])
    assert submit.STRAT_LABEL not in jira.issues[source.stem]["fields"]["labels"]
    before = deepcopy(jira.issues), list(jira.writes)
    assert run(monkeypatch, source) == 0
    assert (jira.issues, jira.writes) == before


@pytest.mark.parametrize("when", ["before", "after"])
def test_unresolved_warning_recovers_without_duplicate_epic_or_comment(unresolved_publication, monkeypatch, when):
    _, source, _, jira = unresolved_publication
    jira.failure = (("POST", "/issue/RHAI-1/comment"), when)
    assert run(monkeypatch, source) == 1
    assert set(jira.issues) == {source.stem, "RHAI-1"}
    assert run(monkeypatch, source) == 0
    assert len(jira.issues["RHAI-1"]["fields"]["comment"]) == 1
    assert set(jira.issues) == {source.stem, "RHAI-1"}
