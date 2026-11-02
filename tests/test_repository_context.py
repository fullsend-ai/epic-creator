from copy import deepcopy
from dataclasses import replace
import json

import pytest

from repository_fixtures import configured, git, git_source, module, strategy
from rhai_config import RepositoryError


def test_uses_only_selected_workload_sources(tmp_path):
    repo = git_source(tmp_path)
    selected = configured(repo)
    source = strategy(tmp_path)
    context = module("repository_context").load_repository_context(selected, source, tmp_path / "artifacts")
    assert context["status"] == "ready"
    assert set(context["inventory"]) == {"operator", "dashboard", "guidelines"}
    assert context["sources"][0]["commit"] == git(repo, "rev-parse", "HEAD")
    assert context["sources"][0]["role"] == "authoritative"


def test_moving_ref_invalidates_bound_context(tmp_path):
    repo = git_source(tmp_path)
    selected, source = configured(repo), strategy(tmp_path)
    api = module("repository_context")
    context = api.load_repository_context(selected, source, tmp_path / "artifacts")
    (repo / "mapping.md").write_text("Updated approved mapping\n")
    git(repo, "add", ".")
    git(repo, "commit", "--signoff", "-m", "Update fixture")
    with pytest.raises(RepositoryError, match="changed"):
        api.assert_context_current(context, selected, source)


def test_context_binds_content_and_source_roles(tmp_path):
    repo = git_source(tmp_path)
    selected, source = configured(repo), strategy(tmp_path)
    api = module("repository_context")
    context = api.load_repository_context(selected, source, tmp_path / "artifacts")
    source.write_text(source.read_text() + "AC-3: New requirement\n")
    with pytest.raises(RepositoryError, match="changed"):
        api.assert_context_current(context, selected, source)
    other = deepcopy(selected.planning.repository_policy)
    other["sources"][0]["role"] = "supporting"
    changed = replace(selected, planning=replace(selected.planning, repository_policy=other))
    assert api.load_repository_context(changed, source, tmp_path / "other")["digest"] != context["digest"]


@pytest.mark.parametrize("mutate", ["duplicate-label", "duplicate-target", "no-approval", "alias-collision"])
def test_inventory_aliases_require_declared_contribution_policy(tmp_path, mutate):
    from repository_fixtures import inventory
    data = inventory()
    if mutate == "duplicate-label": data["repositories"][1]["label"] = "target-repo-operator"
    if mutate == "duplicate-target": data["repositories"][1].update(namespace="example", repository="operator")
    if mutate == "no-approval": data["repositories"][0].pop("approval_reference")
    if mutate == "alias-collision": data["repositories"][0]["aliases"] = ["github.com/example/dashboard"]
    with pytest.raises(RepositoryError):
        module("repository_context").validate_inventory(data)


def test_unavailable_source_returns_explicit_blocked_context(tmp_path):
    repo = git_source(tmp_path)
    selected, source = configured(repo), strategy(tmp_path)
    selected.planning.repository_policy["sources"][0]["paths"] = ["absent.yaml"]
    context = module("repository_context").load_repository_context(selected, source, tmp_path / "artifacts")
    assert context["status"] == "blocked"
    assert context["reason"] == "source_unavailable"
    assert context["inventory"] == {}
