from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from repository_fixtures import ROOT, architecture_checkout, configured, git_source, module, proposal, strategy, epic_metadata
from rhai_config import RepositoryError


@pytest.fixture
def inputs(tmp_path, architecture_checkout):
    repo = git_source(tmp_path)
    source = strategy(tmp_path)
    context = module("repository_context").load_repository_context(configured(repo), source, tmp_path / "artifacts")
    return context, proposal(context)


def test_manager_cloudmanager_grouped_before_epic_files_exist(inputs, tmp_path):
    context, draft = inputs
    plan = module("repository_plan").finalize_repository_plan(context, draft)
    assert plan["ready_for_drafting"] is True
    assert len(plan["scopes"]) == 1
    scope = plan["scopes"][0]
    assert scope["target_repository"]["inventory_key"] == "operator"
    assert scope["work_item_ids"] == ["W1", "W2"]
    assert scope["components"] == ["Operator", "Cloudmanager"]
    assert not (tmp_path / "artifacts/epic-tasks").exists()


def test_source_outage_remains_retryable_and_blocks_drafting(inputs):
    context, draft = inputs
    context = {**context, "status": "blocked", "reason": "source_unavailable", "retryable": True}

    plan = module("repository_plan").finalize_repository_plan(context, draft)

    assert plan["ready_for_drafting"] is False
    assert plan["unresolved"] == [{"work_item_id": None, "reason": "source_unavailable",
                                   "candidates": [], "retryable": True}]


def test_cross_repo_work_keeps_real_dependency(inputs):
    context, draft = inputs
    draft["work_items"][1]["resolution"]["target_key"] = "dashboard"
    draft["work_items"][1]["planned_pr"] = "PR-dashboard"
    draft["work_items"][1]["depends_on"] = ["W1"]
    plan = module("repository_plan").finalize_repository_plan(context, draft)
    assert len(plan["scopes"]) == 2
    by_repo = {s["target_repository"]["inventory_key"]: s for s in plan["scopes"]}
    assert by_repo["dashboard"]["dependencies"] == [by_repo["operator"]["scope_id"]]


def test_epic_gate_must_match_its_repository_scope(inputs):
    context, draft = inputs
    api = module("repository_plan")
    plan = api.finalize_repository_plan(context, draft)
    epics = epic_metadata(plan)
    epics[0]["gated_by"] = "RHAISTRAT-9001-E999"
    with pytest.raises(RepositoryError, match="gate"):
        api.validate_epic_scopes(plan, epics)


@pytest.mark.parametrize("destination,count", [("operator", 1), ("guidelines", 2)])
def test_documentation_follows_publication_repo(inputs, destination, count):
    context, draft = inputs
    draft["work_items"][1].update(delivery_type="docs-authoring")
    draft["work_items"][1]["resolution"]["target_key"] = destination
    if destination != "operator":
        draft["work_items"][1]["planned_pr"] = "PR-docs"
    plan = module("repository_plan").finalize_repository_plan(context, draft)
    assert len(plan["scopes"]) == count
    if count == 1:
        assert plan["scopes"][0]["implementation_type"] is None


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown-dependency", "false-quote", "missing-ac"])
def test_invalid_work_cannot_hide_coverage_or_evidence_gaps(inputs, change):
    context, draft = inputs
    if change == "missing": draft["work_items"].pop()
    if change == "duplicate": draft["work_items"][1]["id"] = "W1"
    if change == "unknown-dependency": draft["work_items"][1]["depends_on"] = ["W999"]
    if change == "false-quote": draft["work_items"][0]["resolution"]["evidence_refs"][0]["quote"] = "invented source text"
    if change == "missing-ac": draft["work_items"][0]["acceptance_criteria"] = []
    with pytest.raises(RepositoryError):
        module("repository_plan").finalize_repository_plan(context, draft)


def test_acceptance_checks_can_also_trace_assigned_requirements(inputs):
    context, draft = inputs
    draft["work_items"][0]["acceptance_criteria"].append(
        {"source_ref": "HLR-1", "text": "Manager emits logs in the operator repository."})
    assert module("repository_plan").finalize_repository_plan(context, draft)["ready_for_drafting"]


def test_independent_conditional_gates_can_have_distinct_prs_in_one_repository(inputs):
    context, draft = inputs
    for number, item in enumerate(list(draft["work_items"]), 1):
        gate = deepcopy(item)
        gate.update(id=f"I{number}", kind="investigation", description=f"Investigate outcome {number}")
        gate.pop("resolution")
        gate.pop("planned_pr")
        draft["work_items"].append(gate)
        item.update(planned_pr=f"PR-{number}", branch="A", gated_by=gate["id"], depends_on=[gate["id"]])
    plan = module("repository_plan").finalize_repository_plan(context, draft)
    assert plan["ready_for_drafting"] is True
    implementations = [s for s in plan["scopes"] if s["kind"] == "implementation"]
    assert len(implementations) == 2
    assert {s["gated_by"] for s in implementations} == {"investigation/I1", "investigation/I2"}


@pytest.mark.parametrize("resolution", [
    {"status": "unresolved", "reason": "publication_destination_unknown", "candidates": []},
    {"status": "resolved", "target_key": "unknown", "evidence_refs": []},
])
def test_unresolved_destination_is_explicit_and_never_defaults(inputs, resolution):
    context, draft = inputs
    draft["work_items"][1]["resolution"] = resolution
    plan = module("repository_plan").finalize_repository_plan(context, draft)
    assert plan["ready_for_drafting"] is True
    assert "target_repository" not in plan["scopes"][0]
    assert plan["scopes"][0]["repository_warning"]
    assert plan["unresolved"][0]["work_item_id"] == "W2"


def test_grouping_cycle_is_reported_not_dropped(inputs):
    context, draft = inputs
    draft["work_items"][1]["resolution"]["target_key"] = "dashboard"
    draft["work_items"][1]["planned_pr"] = "PR-dashboard"
    draft["work_items"][1]["depends_on"] = ["W1"]
    third = deepcopy(draft["work_items"][0])
    third.update(id="W3", depends_on=["W2"])
    draft["work_items"].append(third)
    plan = module("repository_plan").finalize_repository_plan(context, draft)
    assert plan["ready_for_drafting"] is False
    assert any(u["reason"] == "repository_dependency_cycle" for u in plan["unresolved"])


def test_forged_inventory_context_is_rejected(inputs):
    context, draft = inputs
    context["inventory"]["operator"]["repository"] = "dashboard"
    with pytest.raises(RepositoryError):
        module("repository_plan").finalize_repository_plan(context, draft)


def test_explicit_repository_input_conflict_stays_unresolved(inputs):
    context, draft = inputs
    draft["explicit_repositories"] = [{"value": "dashboard", "work_item_ids": ["W1"],
                                       "source_quote": "HLR-1: Manager logs."}]
    plan = module("repository_plan").finalize_repository_plan(context, draft)
    assert plan["ready_for_drafting"] is True
    assert any(u["reason"] == "explicit_repository_conflict" for u in plan["unresolved"])


def test_conditional_pr_keeps_its_gate_beside_unconditional_work_in_the_same_repo(inputs):
    context, draft = inputs
    gate = deepcopy(draft["work_items"][0])
    gate.update(id="I1", kind="investigation", description="Confirm compatible log convention")
    gate.pop("resolution")
    gate.pop("planned_pr")
    draft["work_items"].append(gate)
    draft["work_items"][1].update(planned_pr="PR-cloudmanager", branch="A", gated_by="I1", depends_on=["I1"])
    plan = module("repository_plan").finalize_repository_plan(context, draft)
    implementations = [s for s in plan["scopes"] if s["kind"] == "implementation"]
    assert plan["ready_for_drafting"] is True
    assert len(implementations) == 2
    conditional = next(s for s in implementations if s["planned_pr"] == "PR-cloudmanager")
    assert conditional["work_item_ids"] == ["W2"]
    assert conditional["branch"] == "A"
    assert conditional["gated_by"] == "investigation/I1"
    assert conditional["dependencies"] == ["investigation/I1"]
