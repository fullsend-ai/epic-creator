"""Unresolved PRs must not prevent independent Epic creation."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from repository_fixtures import architecture_checkout, epic_metadata, module, write_repository_decomposition
from test_repository_plan import inputs
from test_repository_pipeline import setup_pipeline
from rhai_config import RepositoryError
import pipeline_repositories
import pipeline_state as pipeline
import check_decompose_progress as progress


def separate_prs(draft, count=8):
    template = deepcopy(draft["work_items"][0])
    for number in range(3, count + 1):
        item = deepcopy(template)
        item["id"] = f"W{number}"
        draft["work_items"].append(item)
    for number, item in enumerate(draft["work_items"], 1):
        item["planned_pr"] = f"PR-{number}"
        if number in (5, 6, 7):
            item["resolution"]["target_key"] = "dashboard"
    draft["work_items"][-1]["resolution"] = {
        "status": "unresolved", "reason": "publication_destination_unknown", "candidates": []}
    return draft


def test_seven_resolved_prs_can_draft_when_eighth_repository_is_unknown(inputs):
    context, draft = inputs
    plan = module("repository_plan").finalize_repository_plan(context, separate_prs(draft))
    assert plan["ready_for_drafting"] is True
    assert [s["scope_id"] for s in plan["scopes"]] == [f"pr/PR-{n}" for n in range(1, 8)]
    assert [u["work_item_id"] for u in plan["unresolved"]] == ["W8"]
    assert plan["partial"] is True
    module("repository_plan").validate_epic_scopes(plan, epic_metadata(plan))


def test_unresolved_dependency_blocks_transitive_dependents_only(inputs):
    context, draft = inputs
    separate_prs(draft, count=5)
    draft["work_items"][1]["depends_on"] = ["W5"]
    draft["work_items"][2]["depends_on"] = ["W2"]
    plan = module("repository_plan").finalize_repository_plan(context, draft)
    assert [s["scope_id"] for s in plan["scopes"]] == ["pr/PR-1", "pr/PR-4"]
    assert {u["work_item_id"] for u in plan["unresolved"]} == {"W2", "W3", "W5"}
    assert plan["ready_for_drafting"] is True


def test_one_unresolved_member_blocks_its_whole_pr_without_dropping_work(inputs):
    context, draft = inputs
    separate_prs(draft, count=3)
    draft["work_items"][0]["planned_pr"] = "PR-3"
    plan = module("repository_plan").finalize_repository_plan(context, draft)
    assert [s["scope_id"] for s in plan["scopes"]] == ["pr/PR-2"]
    assert {u["work_item_id"] for u in plan["unresolved"]} == {"W1", "W3"}


def test_explicit_target_conflict_blocks_only_affected_pr(inputs):
    context, draft = inputs
    separate_prs(draft, count=2)
    draft["work_items"][1]["resolution"] = deepcopy(draft["work_items"][0]["resolution"])
    draft["explicit_repositories"] = [{"value": "dashboard", "work_item_ids": ["W2"],
                                       "source_quote": "HLR-2: Cloudmanager logs."}]
    plan = module("repository_plan").finalize_repository_plan(context, draft)
    assert [s["scope_id"] for s in plan["scopes"]] == ["pr/PR-1"]
    assert plan["ready_for_drafting"] is True
    assert any(u["reason"] == "explicit_repository_conflict" for u in plan["unresolved"])


def test_partial_plan_never_accepts_an_epic_for_blocked_work(inputs):
    context, draft = inputs
    api = module("repository_plan")
    plan = api.finalize_repository_plan(context, separate_prs(draft))
    epics = epic_metadata(plan)
    forbidden = deepcopy(epics[0])
    forbidden.update(epic_id="RHAISTRAT-9001-E008", repository_scope="pr/PR-8", work_item_ids=["W8"])
    with pytest.raises(RepositoryError):
        api.validate_epic_scopes(plan, epics + [forbidden])
    assert len(epics) == 7


def test_pipeline_launches_and_validates_drafting_for_partial_plan(monkeypatch, tmp_path, capsys):
    _, source = setup_pipeline(monkeypatch, tmp_path, capsys)
    pipeline.cmd_next_action([])
    capsys.readouterr()
    api = module("repository_plan")
    paths = api.paths("artifacts", source.stem)
    context = json.loads(paths["context"].read_text())
    from repository_fixtures import proposal
    plan = api.finalize_repository_plan(context, separate_prs(proposal(context)))
    api.atomic_json(paths["plan"], plan)
    assert progress.check_id("plan_repositories", source.stem) == "completed"
    pipeline.cmd_next_action([])
    action = yaml.safe_load(capsys.readouterr().out)
    assert action["phase"] == "DECOMPOSE"
    write_repository_decomposition(source, plan)
    pipeline_repositories.validate_decomposition(pipeline._load_state(), source.stem)
    assert len(list(Path("artifacts/epic-tasks").glob("*-E[0-9][0-9][0-9].md"))) == 7
