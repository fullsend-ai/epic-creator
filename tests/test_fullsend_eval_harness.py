#!/usr/bin/env python3
"""Pin the eval child harness (.fullsend/epic-creator/epic-creator-eval.yaml) to its base.

The eval variant runs the production package (epic-creator.yaml) with no production Jira
credential and the models under evaluation injected from outside. fullsend composes a child over its base
(internal/harness/compose.go): non-empty scalars override, ``env.sandbox`` / ``env.runner``
merge with child keys winning, and ``providers`` entries replace the base entry with the same
provider ``name``. The tests mirror only
that much of the policy and assert the composed eval harness differs from the production file
in exactly the keys the eval needs, so any other drift (a model, an image, a gate) fails CI.
"""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
FULLSEND_DIR = REPO / ".fullsend"
HARNESS_DIR = FULLSEND_DIR / "epic-creator"
PRODUCTION = HARNESS_DIR / "epic-creator.yaml"
EVAL = HARNESS_DIR / "epic-creator-eval.yaml"

EVAL_JIRA_SERVER = "https://redhat.atlassian.net"
EVAL_JIRA_USER = "eval"
EVAL_JIRA_TOKEN = "eval-no-jira"

# Every path at which the composed eval harness may differ from the production file.
EXPECTED_DIFF = {
    "env.sandbox.JIRA_SERVER",
    "env.sandbox.JIRA_USER",
    "env.sandbox.CLAUDE_CODE_SUBAGENT_MODEL",
    "env.runner.JIRA_SERVER",
    "env.runner.JIRA_USER",
    "env.runner.JIRA_TOKEN",
    "providers",
}


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _local(entry):
    """A list entry that is a local file under .fullsend/ (built-ins like vertex-ai are not)."""
    return (FULLSEND_DIR / entry).is_file()


def _identity(entry, key):
    return _load(FULLSEND_DIR / entry)[key] if _local(entry) else entry


def _merge_list(base, child, key):
    child_ids = {_identity(e, key) for e in child}
    return [e for e in base if _identity(e, key) not in child_ids] + list(child)


def _compose(base, child):
    out = dict(base)
    for k, v in child.items():
        if k == "base":
            continue
        if k == "env":
            out["env"] = {
                section: {**base.get("env", {}).get(section, {}), **v.get(section, {})}
                for section in set(base.get("env", {})) | set(v)
            }
        elif k == "providers":
            out[k] = _merge_list(base.get(k, []), v, "name")
        elif v not in (None, "", [], {}):
            out[k] = v
    return out


def _diff(a, b, prefix=""):
    if isinstance(a, dict) and isinstance(b, dict):
        paths = set()
        for k in set(a) | set(b):
            paths |= _diff(a.get(k), b.get(k), f"{prefix}{k}.")
        return paths
    return set() if a == b else {prefix.rstrip(".")}


def test_both_harnesses_parse():
    assert isinstance(_load(PRODUCTION), dict)
    assert isinstance(_load(EVAL), dict)


def test_eval_base_is_the_production_harness():
    # fullsend resolves `base` relative to the child's own directory.
    child = _load(EVAL)
    assert (EVAL.parent / child["base"]).resolve() == PRODUCTION.resolve()


def test_eval_sets_only_the_listed_keys():
    child = _load(EVAL)
    assert set(child) == {"base", "env", "providers"}, (
        "the eval child inherits everything else from epic-creator.yaml; `model` comes from "
        "`fullsend run --model`"
    )
    assert set(child["env"]) == {"sandbox", "runner"}
    # The provider list replaces only the jira entry: nothing else may ride along.
    assert child["providers"] == ["epic-creator/jira-provider-eval.yaml"]


def test_composed_eval_differs_from_production_only_in_listed_keys():
    base = _load(PRODUCTION)
    composed = _compose(base, _load(EVAL))
    assert _diff(base, composed) == EXPECTED_DIFF


def test_composed_provider_swaps_only_the_jira_entry():
    base = _load(PRODUCTION)
    composed = _compose(base, _load(EVAL))
    # The base provider entry is replaced; profiles remain byte-for-byte inherited.
    base_providers = [p for p in base["providers"] if _identity(p, "name") != "jira"]
    assert composed["providers"] == base_providers + ["epic-creator/jira-provider-eval.yaml"]
    assert composed["openshell"]["profiles"] == base["openshell"]["profiles"]


def test_eval_values():
    child = _load(EVAL)
    assert child["env"]["sandbox"] == {
        "JIRA_SERVER": EVAL_JIRA_SERVER,
        "JIRA_USER": EVAL_JIRA_USER,
        "CLAUDE_CODE_SUBAGENT_MODEL": "${AGENT_EVAL_SUBAGENT_MODEL}",
    }
    # Literals, so a host without Jira credentials passes fullsend's env validation.
    assert child["env"]["runner"] == {
        "JIRA_SERVER": EVAL_JIRA_SERVER,
        "JIRA_USER": EVAL_JIRA_USER,
        "JIRA_TOKEN": EVAL_JIRA_TOKEN,
    }


def test_eval_provider_replaces_jira_provider_with_literal_credential():
    base = _load(PRODUCTION)
    composed = _compose(base, _load(EVAL))
    base_jira = [p for p in base["providers"] if _local(p) and _identity(p, "name") == "jira"]
    eval_jira = [p for p in composed["providers"] if _local(p) and _identity(p, "name") == "jira"]
    assert len(base_jira) == 1 and len(eval_jira) == 1
    assert eval_jira[0] != base_jira[0]
    prod_provider = _load(FULLSEND_DIR / base_jira[0])
    eval_provider = _load(FULLSEND_DIR / eval_jira[0])
    assert eval_provider["type"] == prod_provider["type"]
    assert eval_provider["credentials"] == {"JIRA_TOKEN": EVAL_JIRA_TOKEN}
    # Built-in providers (vertex-ai) are inherited unchanged.
    assert [p for p in composed["providers"] if not _local(p)] == [
        p for p in base["providers"] if not _local(p)
    ]


def test_eval_inherits_production_read_only_jira_profile():
    base = _load(PRODUCTION)
    composed = _compose(base, _load(EVAL))
    assert composed["openshell"]["profiles"] == base["openshell"]["profiles"]
    (jira_path,) = [p for p in composed["openshell"]["profiles"] if _identity(p, "id") == "jira"]
    profile = _load(FULLSEND_DIR / jira_path)
    assert profile["endpoints"]
    assert all(endpoint["enforcement"] == "enforce" for endpoint in profile["endpoints"])
    assert all(endpoint["access"] == "read-only" for endpoint in profile["endpoints"])


def test_eval_provider_credentials_declared_by_profile():
    composed = _compose(_load(PRODUCTION), _load(EVAL))
    profiles = {
        _identity(p, "id"): _load(FULLSEND_DIR / p) for p in composed["openshell"]["profiles"]
    }
    for entry in composed["providers"]:
        if not _local(entry):
            continue
        provider = _load(FULLSEND_DIR / entry)
        declared = {
            var
            for cred in profiles[provider["type"]].get("credentials", [])
            for var in cred.get("env_vars", [])
        }
        assert set(provider.get("credentials", {})) <= declared


def test_config_registers_eval_harness():
    sources = [a.get("source") for a in _load(FULLSEND_DIR / "config.yaml")["agents"]]
    assert "epic-creator/epic-creator.yaml" in sources
    assert "epic-creator/epic-creator-eval.yaml" in sources
