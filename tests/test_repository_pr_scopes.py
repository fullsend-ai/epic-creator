"""Epic boundaries follow planned PRs, including multiple PRs in one repo."""
from copy import deepcopy

import pytest

from repository_fixtures import architecture_checkout, epic_metadata, module
from test_repository_plan import inputs
from rhai_config import RepositoryError


def planned(draft):
    for item in draft["work_items"]:
        item["planned_pr"] = "PR-logging"
    return draft


def test_separate_planned_prs_in_one_repository_keep_their_dependency(inputs):
    context, draft = inputs
    planned(draft)
    draft["work_items"][1].update(planned_pr="PR-cloudmanager", depends_on=["W1"])

    plan = module("repository_plan").finalize_repository_plan(context, draft)

    assert plan["ready_for_drafting"] is True
    by_pr = {scope["planned_pr"]: scope for scope in plan["scopes"]}
    assert set(by_pr) == {"PR-logging", "PR-cloudmanager"}
    assert by_pr["PR-logging"]["work_item_ids"] == ["W1"]
    assert by_pr["PR-cloudmanager"]["work_item_ids"] == ["W2"]
    assert by_pr["PR-cloudmanager"]["dependencies"] == ["pr/PR-logging"]
    assert {s["target_repository"]["inventory_key"] for s in plan["scopes"]} == {"operator"}
    module("repository_plan").validate_epic_scopes(plan, epic_metadata(plan))


def test_one_planned_pr_can_cover_several_work_items(inputs):
    context, draft = inputs
    plan = module("repository_plan").finalize_repository_plan(context, planned(draft))
    assert len(plan["scopes"]) == 1
    assert plan["scopes"][0]["planned_pr"] == "PR-logging"
    assert plan["scopes"][0]["work_item_ids"] == ["W1", "W2"]


def test_multiple_repositories_can_have_more_epics_than_repositories(inputs):
    context, draft = inputs
    planned(draft)
    draft["work_items"][1].update(planned_pr="PR-cloudmanager", depends_on=["W1"])
    third = deepcopy(draft["work_items"][0])
    third.update(id="W3", planned_pr="PR-dashboard", depends_on=["W2"])
    third["resolution"]["target_key"] = "dashboard"
    draft["work_items"].append(third)

    api = module("repository_plan")
    plan = api.finalize_repository_plan(context, draft)
    assert plan["ready_for_drafting"] is True
    assert len(plan["scopes"]) == 3
    by_pr = {s["planned_pr"]: s for s in plan["scopes"]}
    assert by_pr["PR-dashboard"]["dependencies"] == ["pr/PR-cloudmanager"]
    epics = epic_metadata(plan)
    api.validate_epic_scopes(plan, epics)
    with pytest.raises(RepositoryError, match="omits"):
        api.validate_epic_scopes(plan, epics[:-1])
    with pytest.raises(RepositoryError, match="duplicate"):
        api.validate_epic_scopes(plan, epics + [deepcopy(epics[0])])


def test_a_planned_pr_cannot_modify_two_repositories(inputs):
    context, draft = inputs
    planned(draft)
    draft["work_items"][1]["resolution"]["target_key"] = "dashboard"
    with pytest.raises(RepositoryError, match="single repository"):
        module("repository_plan").finalize_repository_plan(context, draft)


def test_implementation_requires_an_explicit_planned_pr(inputs):
    context, draft = inputs
    planned(draft)
    draft["work_items"][1].pop("planned_pr")
    with pytest.raises(RepositoryError, match="planned_pr.*required"):
        module("repository_plan").finalize_repository_plan(context, draft)


def test_a_planned_pr_cannot_hide_conditional_work_inside_an_unconditional_epic(inputs):
    context, draft = inputs
    planned(draft)
    gate = deepcopy(draft["work_items"][0])
    gate.update(id="I1", kind="investigation")
    gate.pop("resolution")
    gate.pop("planned_pr")
    draft["work_items"].append(gate)
    draft["work_items"][1].update(branch="A", gated_by="I1", depends_on=["I1"])
    with pytest.raises(RepositoryError, match="conditional"):
        module("repository_plan").finalize_repository_plan(context, draft)
