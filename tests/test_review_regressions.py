"""Regression coverage for repository planning and publication review findings."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

from repository_fixtures import architecture_checkout
from test_repository_plan import inputs
from test_repository_submit import publication, unresolved_publication, run
import pipeline_repositories
import repository_context
import repository_plan
import rhai_config
import submit


@pytest.mark.parametrize("failed_review", [False, True])
def test_unready_strategy_does_not_block_ready_strategy(publication, monkeypatch, failed_review):
    import repository_submit
    calls, published = [], []
    monkeypatch.setattr(submit, "_find_submittable_strats", lambda *_: ["RHAISTRAT-1", "RHAISTRAT-2"])
    monkeypatch.setattr(submit, "_check_review_passed", lambda _, key: (
        not (failed_review and key == "RHAISTRAT-1"), {"score": 14}))

    def preflight(selected, key, root):
        calls.append(key)
        if key == "RHAISTRAT-1":
            raise rhai_config.RepositoryError("stale source")
        return {"plan": []}

    def publish(*args, **kwargs):
        assert calls == (["RHAISTRAT-2"] if failed_review else ["RHAISTRAT-1", "RHAISTRAT-2"])
        assert kwargs["repository_submission"] == {"plan": []}
        published.append(args[3])
        return 0, 0

    monkeypatch.setattr(repository_submit, "preflight", preflight)
    monkeypatch.setattr(submit, "_scan_epics", lambda *_: [object()])
    monkeypatch.setattr(submit, "_submit_strategy", publish)
    monkeypatch.setattr(sys, "argv", ["submit.py", "--all"])
    if failed_review:
        submit.main()
    else:
        with pytest.raises(SystemExit) as exc:
            submit.main()
        assert exc.value.code == 1
    assert published == ["RHAISTRAT-2"]


def test_missing_artifacts_directory_fails(publication, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["submit.py", "--all", "--artifacts-dir", "missing"])
    with pytest.raises(SystemExit) as exc:
        submit.main()
    assert exc.value.code == 1
    assert publication[-1].writes == []


def test_planning_retry_refreshes_blocked_context(publication, monkeypatch):
    selected, source, context, _, _ = publication
    paths = repository_plan.paths("artifacts", source.stem)
    blocked = {**context, "status": "blocked", "retryable": True}
    repository_plan.atomic_json(paths["context"], blocked)
    calls = []

    def fetch(*args):
        calls.append(True)
        return context

    monkeypatch.setattr(repository_context, "load_repository_context", fetch)
    monkeypatch.setattr(pipeline_repositories, "bound_config", lambda _: selected)
    refreshed = pipeline_repositories.repository_plan_for({}, source.stem, prepare=True)
    assert calls and refreshed == context
    assert pipeline_repositories._json(paths["context"]) == context


def test_planning_retry_preserves_repeated_failure(publication, monkeypatch):
    selected, source, context, _, _ = publication
    paths = repository_plan.paths("artifacts", source.stem)
    blocked = {**context, "status": "blocked", "reason": "source_unavailable", "retryable": True}
    repository_plan.atomic_json(paths["context"], blocked)
    monkeypatch.setattr(repository_context, "load_repository_context", lambda *args: blocked)
    monkeypatch.setattr(pipeline_repositories, "bound_config", lambda _: selected)
    with pytest.raises(rhai_config.RepositoryError, match="unresolved"):
        pipeline_repositories.repository_plan_for({}, source.stem, prepare=True)
    assert pipeline_repositories._json(paths["context"]) == blocked
    assert pipeline_repositories._json(paths["plan"])["ready_for_drafting"] is False


@pytest.mark.parametrize("field,reference", [("requirement_refs", "AC-1"), ("acceptance_refs", "HLR-1")])
def test_references_must_match_catalog_kind(inputs, field, reference):
    context, draft = inputs
    draft["work_items"][0][field] = [reference]
    with pytest.raises(rhai_config.RepositoryError, match="wrong source kind"):
        repository_plan.finalize_repository_plan(context, draft)


def test_conditional_investigation_keeps_branch(inputs):
    context, draft = inputs
    for n in (1, 2, 3):
        item = deepcopy(draft["work_items"][0])
        item.update(id=f"I{n}", kind="investigation")
        item.pop("resolution")
        item.pop("planned_pr")
        if n > 1:
            item.update(branch="A" if n == 2 else "B", gated_by="I1", depends_on=["I1"])
        draft["work_items"].append(item)
    plan = repository_plan.finalize_repository_plan(context, draft)
    scope = next(s for s in plan["scopes"] if s["scope_id"] == "investigation/I2")
    assert plan["ready_for_drafting"] is True
    assert scope["branch"] == "A" and scope["gated_by"] == "investigation/I1"
    from repository_fixtures import epic_metadata
    repository_plan.validate_epic_scopes(plan, epic_metadata(plan))
    scenarios = list(repository_plan._scenarios(plan["scopes"]))
    assert len(scenarios) == 2
    assert sum(scope in scenario for scenario in scenarios) == 1


def test_normalized_warning_is_not_posted_again(unresolved_publication, monkeypatch):
    _, source, _, jira = unresolved_publication
    assert run(monkeypatch, source) == 0
    comments = jira.issues["RHAI-1"]["fields"]["comment"]
    comments[0]["body"]["content"][0]["attrs"] = {}
    assert run(monkeypatch, source) == 0
    assert len(comments) == 1


def test_html_report_invalid_status_overrides_old_passing_review(publication):
    _, source, _, _, _ = publication
    import pipeline_status
    import generate_html_report
    pipeline_status.write_status(source.stem, "invalid", "PLAN_REPOSITORIES", "Invalid source <details>")
    html, _, stats = generate_html_report.render_strategy_section(source.stem)
    assert stats["error"] and not stats["passed"] and not stats["failed"]
    assert "Invalid source &lt;details&gt;" in html
    report = generate_html_report.build_report([source.stem], "2026-11-11")
    assert "Invalid source &lt;details&gt;" in report
