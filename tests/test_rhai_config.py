from pathlib import Path
import importlib
import importlib.util
import json
import sys

import pytest
import yaml

from repository_fixtures import git_source, policy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def config_module():
    assert importlib.util.find_spec("rhai_config") is not None, "Missing fixed RHAI configuration loader"
    return importlib.import_module("rhai_config")


def test_fixed_rhai_config_has_no_descriptor_dependency(tmp_path):
    api = config_module()
    repo = git_source(tmp_path)
    path = tmp_path / "rhai.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, "enabled": True, "repository_policy": policy(repo)}))
    selected = api.load_config(path)
    assert selected.planning.repository_policy["sources"][0]["url"] == repo.as_uri()
    assert selected.project == "RHAISTRAT"
    assert selected.submission.project == "RHAI"
    assert importlib.util.find_spec("workload") is None
    before = selected.digest
    data = yaml.safe_load(path.read_text())
    data["repository_policy"]["sources"][0]["role"] = "supporting"
    path.write_text(yaml.safe_dump(data))
    assert api.load_config(path).digest != before


@pytest.mark.parametrize("change", ["duplicate-source", "unknown-inventory", "unknown-guidance", "unsafe-path", "unknown-option"])
def test_incomplete_or_unsafe_configuration_is_rejected(tmp_path, change):
    api = config_module()
    repo = git_source(tmp_path)
    data = {"schema_version": 1, "enabled": True, "repository_policy": policy(repo)}
    p = data["repository_policy"]
    if change == "duplicate-source": p["sources"].append(dict(p["sources"][0]))
    elif change == "unknown-inventory": p["inventory"]["source_id"] = "missing"
    elif change == "unknown-guidance": p["guidance"] = {"source_id": "missing", "path": "guide.md"}
    elif change == "unsafe-path": p["inventory"]["path"] = "../escape"
    else: data["workload"] = "other"
    path = tmp_path / "rhai.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(api.RepositoryError):
        api.load_config(path)


def test_disabled_policy_preserves_existing_rhai_behavior():
    selected = config_module().load_config()
    assert selected.planning.repository_policy is None
