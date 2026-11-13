from repository_fixtures import write_artifact
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from repository_fixtures import architecture_checkout, configured, epic_metadata, git_source, proposal, strategy, write_repository_decomposition
from test_repository_pipeline import setup_pipeline
import artifact_utils as artifacts
import pipeline_repositories
import repository_context
import repository_plan
from rhai_config import RepositoryError


@pytest.fixture
def plan(tmp_path, architecture_checkout):
    repo = git_source(tmp_path)
    context = repository_context.load_repository_context(configured(repo), strategy(tmp_path), tmp_path / "artifacts")
    return repository_plan.finalize_repository_plan(context, proposal(context))


@pytest.mark.parametrize("change", ["duplicate", "target", "work", "component", "type", "snapshot", "dependency"])
def test_producer_scope_contract_rejects_invalid_epics(plan, change):
    assert hasattr(repository_plan, "validate_epic_scopes"), "Missing repository epic validation"
    epics = epic_metadata(plan)
    if change == "duplicate": epics.append(deepcopy(epics[0]))
    if change == "target": epics[0]["target_repository"]["repository"] = "dashboard"
    if change == "work": epics[0]["work_item_ids"] = ["W1"]
    if change == "component": epics[0]["components"] = ["Operator"]
    if change == "type": epics[0]["type"] = "Investigation"
    if change == "snapshot": epics[0]["mapping_snapshot_digest"] = "stale"
    if change == "dependency": epics[0]["dependencies"] = ["other"]
    with pytest.raises(RepositoryError):
        repository_plan.validate_epic_scopes(plan, epics)


def test_valid_repo_scope_keeps_multiple_components(plan):
    assert hasattr(repository_plan, "validate_epic_scopes"), "Missing repository epic validation"
    epics = epic_metadata(plan)
    repository_plan.validate_epic_scopes(plan, epics)
    assert epics[0]["components"] == ["Operator", "Cloudmanager"]


def test_review_digest_changes_with_body_but_not_submission_key(plan, tmp_path):
    assert hasattr(repository_plan, "decomposition_digest"), "Missing reviewed content binding"
    root = tmp_path / "output"
    epic = epic_metadata(plan)[0]
    path = root / f"epic-tasks/{epic['epic_id']}.md"
    write_artifact(path, epic, "epic-task", body="Operator implementation ACs\n")
    before = repository_plan.decomposition_digest(plan, root, plan["source_key"])
    artifacts.update_frontmatter(path, {"jira_key": "RHAI-9001"}, "epic-task")
    assert repository_plan.decomposition_digest(plan, root, plan["source_key"]) == before
    data, _ = artifacts.read_frontmatter(path)
    write_artifact(path, data, "epic-task", body="Also modify dashboard\n")
    assert repository_plan.decomposition_digest(plan, root, plan["source_key"]) != before


def test_frontmatter_round_trip_and_missing_repo_metadata_fail(monkeypatch, tmp_path, capsys):
    selected, source = setup_pipeline(monkeypatch, tmp_path, capsys)
    context = repository_context.load_repository_context(selected, source, tmp_path / "artifacts")
    plan = repository_plan.finalize_repository_plan(context, proposal(context))
    provenance = {k: v for k, v in artifacts.read_frontmatter(source)[0].items()
                  if k in artifacts._REPOSITORY_SOURCE_FIELDS}
    data = {**provenance, **epic_metadata(plan)[0]}
    missing = deepcopy(data)
    missing.pop("target_repository")
    assert any("target_repository" in message for message in artifacts.validate(missing, "epic-task"))
    path = tmp_path / f"artifacts/epic-tasks/{data['epic_id']}.md"
    write_artifact(path, data, "epic-task", body="Repository implementation\n")
    read, _ = artifacts.read_frontmatter_validated(path, "epic-task")
    assert read["components"] == ["Operator", "Cloudmanager"]
    assert read["target_repository"]["inventory_key"] == "operator"


def test_passing_review_is_bound_to_exact_repo_plan_and_prose(monkeypatch, tmp_path, capsys):
    selected, source = setup_pipeline(monkeypatch, tmp_path, capsys)
    context = repository_context.load_repository_context(selected, source, tmp_path / "artifacts")
    plan = repository_plan.finalize_repository_plan(context, proposal(context))
    locations = repository_plan.paths("artifacts", source.stem)
    repository_plan.atomic_json(locations["context"], context)
    repository_plan.atomic_json(locations["plan"], plan)
    provenance = write_repository_decomposition(source, plan)
    review_path = Path(f"artifacts/epic-reviews/{source.stem}-decomp-review.md")
    review = {**provenance, "strat_id": source.stem, "score": 14, "pass": True,
              "recommendation": "accept",
              "repository_plan_digest": plan["digest"],
              "reviewed_decomposition_digest": repository_plan.decomposition_digest(plan, Path("artifacts"), source.stem)}
    write_artifact(review_path, review, "decomp-review", body="All criteria verified\n")
    state = __import__("pipeline_state")._load_state()
    pipeline_repositories.validate_artifact(state, source.stem, review_path, "decomp-review")
    epic_path = Path(f"artifacts/epic-tasks/{epic_metadata(plan)[0]['epic_id']}.md")
    epic_path.write_text(epic_path.read_text() + "Also modify dashboard source code\n")
    with pytest.raises(RepositoryError, match="review|Review"):
        pipeline_repositories.validate_artifact(state, source.stem, review_path, "decomp-review")


def reviewed_decomposition(monkeypatch, tmp_path, capsys):
    selected, source = setup_pipeline(monkeypatch, tmp_path, capsys)
    context = repository_context.load_repository_context(selected, source, tmp_path / "artifacts")
    plan = repository_plan.finalize_repository_plan(context, proposal(context))
    locations = repository_plan.paths("artifacts", source.stem)
    repository_plan.atomic_json(locations["context"], context)
    repository_plan.atomic_json(locations["plan"], plan)
    metadata = write_repository_decomposition(source, plan)
    review_path = Path(f"artifacts/epic-reviews/{source.stem}-decomp-review.md")
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text("Reviewed all seven criteria against original source and repository evidence.\n")
    return source, plan, metadata, review_path


def test_frontmatter_completion_stamps_reviewed_content(monkeypatch, tmp_path, capsys):
    import frontmatter
    source, plan, _, review = reviewed_decomposition(monkeypatch, tmp_path, capsys)
    monkeypatch.setattr(sys, "argv", ["frontmatter.py", "set", "--source", str(source), str(review),
                         f"strat_id={source.stem}", "score=14", "pass=true", "recommendation=accept"])
    try:
        frontmatter.main()
    except SystemExit as exc:
        assert exc.code == 0, capsys.readouterr().err
    data, _ = artifacts.read_frontmatter(review)
    assert data["repository_plan_digest"] == plan["digest"]
    assert data["reviewed_decomposition_digest"] == repository_plan.decomposition_digest(plan, Path("artifacts"), source.stem)


def test_revision_content_change_forces_rereview_despite_false_flag(monkeypatch, tmp_path, capsys):
    import pipeline_state
    source, plan, metadata, review_path = reviewed_decomposition(monkeypatch, tmp_path, capsys)
    review = {**metadata, "strat_id": source.stem, "score": 14, "pass": True,
              "recommendation": "accept",
              "repository_plan_digest": plan["digest"],
              "reviewed_decomposition_digest": repository_plan.decomposition_digest(plan, Path("artifacts"), source.stem)}
    write_artifact(review_path, review, "decomp-review", body="Reviewed all seven criteria\n")
    path = Path(f"artifacts/epic-tasks/{epic_metadata(plan)[0]['epic_id']}.md")
    path.write_text(path.read_text() + "\nNew scope\n")
    state = pipeline_state._load_state()
    state["phase"] = "RE_REVIEW_CHECK"
    next_phase, _ = pipeline_state.advance(state)
    assert next_phase == "RE_REVIEW"


@pytest.mark.parametrize("phase", ["REVISE_DECOMP", "RE_REVISE"])
@pytest.mark.parametrize("revised", [False, True])
def test_dispatcher_accepts_completed_revision_then_requests_fresh_review(monkeypatch, tmp_path, capsys, phase, revised):
    import pipeline_state
    import yaml
    source, plan, metadata, review_path = reviewed_decomposition(monkeypatch, tmp_path, capsys)
    review = {**metadata, "strat_id": source.stem, "score": 10, "pass": False,
              "recommendation": "revise", "repository_plan_digest": plan["digest"],
              "reviewed_decomposition_digest": repository_plan.decomposition_digest(plan, Path("artifacts"), source.stem)}
    write_artifact(review_path, review, "decomp-review", body="Improve acceptance criteria\n")
    state = pipeline_state._load_state()
    state.update(phase=phase, revise_cycle=1 if phase == "RE_REVISE" else 0)
    pipeline_state._write_ids("tmp/pipeline-revise-ids.txt", [source.stem])
    pipeline_state._save_state(state)
    pipeline_state._reset_revised_flag(f"artifacts/epic-tasks/{source.stem}-decomposition.md")
    pipeline_repositories.validate_inputs(state, phase, [source.stem])
    path = Path(f"artifacts/epic-tasks/{epic_metadata(plan)[0]['epic_id']}.md")
    path.write_text(path.read_text() + "\nAcceptance criteria clarified.\n")
    summary = Path(f"artifacts/epic-tasks/{source.stem}-decomposition.md")
    artifacts.update_frontmatter(summary, {"revised": revised}, "decomp-summary")
    capsys.readouterr()
    pipeline_state.cmd_next_action([])
    action = yaml.safe_load(capsys.readouterr().out)
    assert action["phase"] == "RE_REVIEW"
    assert action["action"] == "launch_wave"
    assert not review_path.exists()
    assert pipeline_state.read_status(source.stem) is None


def test_revision_rejects_stale_review_before_worker_completion(monkeypatch, tmp_path, capsys):
    import pipeline_state
    source, plan, metadata, review_path = reviewed_decomposition(monkeypatch, tmp_path, capsys)
    review = {**metadata, "strat_id": source.stem, "score": 10, "pass": False,
              "recommendation": "revise", "repository_plan_digest": plan["digest"],
              "reviewed_decomposition_digest": repository_plan.decomposition_digest(plan, Path("artifacts"), source.stem)}
    write_artifact(review_path, review, "decomp-review", body="Improve acceptance criteria\n")
    pipeline_state._reset_revised_flag(f"artifacts/epic-tasks/{source.stem}-decomposition.md")
    path = Path(f"artifacts/epic-tasks/{epic_metadata(plan)[0]['epic_id']}.md")
    path.write_text(path.read_text() + "\nChanged before revision.\n")
    with pytest.raises(RepositoryError, match="Review no longer matches"):
        pipeline_repositories.validate_inputs(pipeline_state._load_state(), "REVISE_DECOMP", [source.stem])


def test_drafter_validation_checks_epics_as_well_as_plan(monkeypatch, tmp_path, capsys):
    source, plan, _, _ = reviewed_decomposition(monkeypatch, tmp_path, capsys)
    monkeypatch.setattr(sys, "argv", ["repository_plan.py", "validate", "--source", source.stem, "--include-epics"])
    assert repository_plan.main() == 0
    path = Path(f"artifacts/epic-tasks/{epic_metadata(plan)[0]['epic_id']}.md")
    artifacts.update_frontmatter(path, {"gated_by": "RHAISTRAT-9001-E999"}, "epic-task")
    assert repository_plan.main() == 1
    assert "gate conflicts" in capsys.readouterr().err


def test_revision_check_reports_broken_repository_binding(monkeypatch, tmp_path, capsys):
    import pipeline_state
    source, _, _, _ = reviewed_decomposition(monkeypatch, tmp_path, capsys)
    state = pipeline_state._load_state()
    state["phase"] = "RE_REVIEW_CHECK"
    repository_plan.paths("artifacts", source.stem)["plan"].unlink()
    pipeline_state.advance(state)
    status = pipeline_state.read_status(source.stem)
    assert status and status["status"] == "invalid"


def test_drafting_cannot_supply_its_own_completed_review(monkeypatch, tmp_path, capsys):
    import pipeline_state
    _, _, _, review = reviewed_decomposition(monkeypatch, tmp_path, capsys)
    review.write_text("Drafting worker tried to pre-create a passing review.\n")
    state = pipeline_state._load_state()
    state["phase"] = "DECOMPOSE"
    next_phase, _ = pipeline_state.advance(state)
    assert next_phase == "REVIEW_DECOMP"
    assert not review.exists()


def test_dependency_revision_preserves_repository_scopes_and_forces_review(monkeypatch, tmp_path, capsys):
    import pipeline_state
    source, old, metadata, review_path = reviewed_decomposition(monkeypatch, tmp_path, capsys)
    review = {**metadata, "strat_id": source.stem, "score": 13, "pass": True,
              "recommendation": "accept", "repository_plan_digest": old["digest"],
              "reviewed_decomposition_digest": repository_plan.decomposition_digest(old, Path("artifacts"), source.stem)}
    write_artifact(review_path, review, "decomp-review", body="Add the missing readiness gate.\n")
    locations = repository_plan.paths("artifacts", source.stem)
    context = json.loads(locations["context"].read_text())
    # Reproduce the live run: an independent Investigation and one planned PR,
    # followed by a review requiring the PR to depend on the Investigation.
    draft = deepcopy(old["proposal"])
    investigation = deepcopy(draft["work_items"][0])
    investigation.update(id="I1", kind="investigation")
    investigation.pop("planned_pr")
    investigation.pop("resolution")
    draft["work_items"].append(investigation)
    old = repository_plan.finalize_repository_plan(context, draft)
    repository_plan.atomic_json(locations["plan"], old)
    write_repository_decomposition(source, old)
    review.update(repository_plan_digest=old["digest"],
                  reviewed_decomposition_digest=repository_plan.decomposition_digest(old, Path("artifacts"), source.stem))
    write_artifact(review_path, review, "decomp-review", body="Add prerequisite edge.\n")
    for item in draft["work_items"][:2]:
        item.update(depends_on=["I1"], gated_by="I1")
    repository_plan.atomic_json(locations["proposal"], draft)
    monkeypatch.setattr(sys, "argv", ["repository_plan.py", "revise-dependencies", "--source", source.stem])
    assert repository_plan.main() == 0
    new = json.loads(locations["plan"].read_text())
    assert new["digest"] != old["digest"]
    write_repository_decomposition(source, new)
    with pytest.raises(RepositoryError, match="Review no longer matches"):
        pipeline_repositories.validate_artifact(pipeline_state._load_state(), source.stem,
                                               review_path, "decomp-review")
    artifacts.update_frontmatter(f"artifacts/epic-tasks/{source.stem}-decomposition.md", {"revised": False}, "decomp-summary")
    state = pipeline_state._load_state()
    state["phase"] = "REVISE_DECOMP"
    pipeline_state._save_state(state)
    capsys.readouterr()
    pipeline_state.cmd_next_action([])
    action = __import__("yaml").safe_load(capsys.readouterr().out)
    assert action["phase"] == "RE_REVIEW"
    assert action["action"] == "launch_wave"
    assert pipeline_state.read_status(source.stem) is None
    assert not review_path.exists()
