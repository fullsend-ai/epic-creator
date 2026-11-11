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
    submission = repository_submit.preflight(selected, source.stem, Path("artifacts"))
    with repository_submit.publication(submission, Path("artifacts"),
                                       ("https://jira.example.test", "synthetic", "synthetic")) as (create, _):
        keys, errors = submit._create_epics("https://jira.example.test", "synthetic", "synthetic",
                                           submission["plan"], source.stem, create=create)
    assert errors == 0
    fields = jira.issues[keys[f"{source.stem}-E001"]]["fields"]
    assert fields["components"] == [{"name": "Operator"}, {"name": "Cloudmanager"}]
    assert "https://github.com/example/operator" in json.dumps(fields["description"])
    assert fields["customfield_10933"] == "https://github.com/example/operator"
    assert not any(label.startswith("target-repo-") for label in fields["labels"])


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
    assert fields["customfield_10933"] == "https://github.com/example/operator"
    assert not any(label.startswith("target-repo-") for label in fields["labels"])
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


@pytest.mark.parametrize("url", [None, "", "https://github.com/example/dashboard"])
def test_recovery_rejects_changed_repository_url_without_mutation(publication, monkeypatch, url):
    _, source, _, _, jira = publication
    assert run(monkeypatch, source) == 0
    fields = jira.issues["RHAI-1"]["fields"]
    fields["customfield_10933"] = url
    fields["labels"].append("keep-me")
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


def test_recovery_accepts_jira_empty_adf_attributes(publication, monkeypatch):
    _, source, _, _, jira = publication
    jira.failure = (("POST", "/issue"), "after")
    assert run(monkeypatch, source) == 1
    description = jira.issues["RHAI-1"]["fields"]["description"]
    paragraph = next(node for node in description["content"] if node["type"] == "paragraph")
    assert "attrs" not in paragraph
    paragraph["attrs"] = {}
    jira.failure = None
    assert run(monkeypatch, source) == 0
    assert set(jira.issues) == {source.stem, "RHAI-1"}
    assert "epic-creator-auto-decomposed" in jira.issues[source.stem]["fields"]["labels"]


@pytest.mark.parametrize("change", ["empty_attrs", "text", "nonempty_attrs", "marks"])
def test_verify_jira_table_normalization(publication, change):
    selected, source, _, _, _ = publication
    entry = repository_submit.preflight(selected, source.stem, Path("artifacts"))["plan"][0]
    entry["repo_url_field"] = "customfield_10933"
    desired = {"type": "doc", "version": 1, "content": [{"type": "table", "content": [
        {"type": "tableRow", "content": [{"type": "tableCell", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Reviewed scope"}]}]}]}]}]}
    entry["desired"]["description_adf"] = desired
    fields = {"project": {"key": "RHAI"}, "issuetype": {"name": "Epic"},
              "labels": [entry["identity"], entry["fingerprint"]], "parent": {"key": source.stem},
              entry["repo_url_field"]: entry["repo_url"], "summary": entry["desired"]["summary"],
              "components": [{"name": name} for name in entry["desired"]["components"]],
              "description": deepcopy(desired)}
    cell = fields["description"]["content"][0]["content"][0]["content"][0]
    cell["attrs"] = {}
    if change == "text":
        cell["content"][0]["content"][0]["text"] = "Changed scope"
    elif change == "nonempty_attrs":
        cell["attrs"] = {"colspan": 2}
    elif change == "marks":
        cell["content"][0]["content"][0]["marks"] = [{"type": "strong"}]
    if change == "empty_attrs":
        repository_submit._verify_issue(entry, {"key": "RHAI-1", "fields": fields}, source.stem)
    else:
        with pytest.raises(repository_submit.RepositoryError, match="published repository scope"):
            repository_submit._verify_issue(entry, {"key": "RHAI-1", "fields": fields}, source.stem)


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
    assert fields.get("customfield_10933") is None
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
    assert fields.get("customfield_10933") is None
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


@pytest.mark.parametrize("problem", ["missing", "duplicate", "text", "noncustom"])
def test_invalid_repo_url_field_prevents_all_jira_writes(publication, monkeypatch, problem):
    _, source, _, _, jira = publication
    if problem == "missing":
        jira.repo_fields = []
    elif problem == "duplicate":
        jira.repo_fields.append(deepcopy(jira.repo_fields[0]))
    elif problem == "text":
        jira.repo_fields[0]["schema"]["custom"] = "com.atlassian.jira.plugin.system.customfieldtypes:textfield"
    else:
        jira.repo_fields[0]["fieldId"] = "url"
    assert run(monkeypatch, source) == 1
    assert jira.writes == []


def test_repository_url_field_uses_project_metadata_and_paginates(monkeypatch):
    calls = []
    def metadata(server, path, user, token):
        calls.append(path)
        if path.startswith("/issue/createmeta/RHAI/issuetypes?"):
            return {"issueTypes": [{"id": "10000", "name": "Epic"}], "total": 1}
        if "startAt=0" in path:
            return {"fields": [{"fieldId": "summary", "name": "Summary"}], "total": 2}
        return {"fields": [{"fieldId": "customfield_10933", "name": "URL",
                            "schema": {"type": "string", "custom": "com.atlassian.jira.plugin.system.customfieldtypes:url"}}], "total": 2}
    monkeypatch.setattr(repository_submit.jira, "api_call_with_retry", metadata)
    assert repository_submit._repository_url_field(("https://jira.example.test", "synthetic", "synthetic")) == "customfield_10933"
    assert "startAt=1" in calls[-1]


def test_unresolved_recovery_rejects_an_invented_repo_url(unresolved_publication, monkeypatch):
    _, source, _, jira = unresolved_publication
    assert run(monkeypatch, source) == 0
    jira.issues["RHAI-1"]["fields"]["customfield_10933"] = "https://github.com/example/guessed"
    before = deepcopy(jira.issues), list(jira.writes)
    assert run(monkeypatch, source) == 1
    assert (jira.issues, jira.writes) == before


def test_repository_url_field_ignores_same_named_fields(publication, monkeypatch):
    _, source, _, _, jira = publication
    jira.repo_fields.insert(0, {**jira.repo_fields[0], "fieldId": "customfield_99902"})
    assert run(monkeypatch, source) == 0
    fields = jira.issues["RHAI-1"]["fields"]
    assert fields["customfield_10933"] == "https://github.com/example/operator"
    assert "customfield_99902" not in fields


def test_same_named_url_field_cannot_replace_existing_field(publication, monkeypatch):
    _, source, _, _, jira = publication
    jira.repo_fields[0]["fieldId"] = "customfield_99902"
    assert run(monkeypatch, source) == 1
    assert jira.writes == []
