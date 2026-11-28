#!/usr/bin/env python3
"""Tests for skills/repository-resolution/scripts/lookup.py — Phase 1 and details."""
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "repository-resolution" / "scripts"))
import lookup  # noqa: E402
from lookup import ResolutionError  # noqa: E402

MAP_PATH = "architecture/v1/component-map.json"
MAPPING_PATH = "config/rhai/component-repositories.yaml"

COMPONENTS = {
    "odh-dashboard": {"key": "odh-dashboard", "repo_org": "red-hat-data-services",
                      "repo_name": "odh-dashboard"},
    "kserve": {"key": "kserve", "repo_org": "red-hat-data-services",
               "repo_name": "kserve"},
    "praxis-policy": {"key": "praxis-policy", "repo_org": "praxis-proxy",
                      "repo_name": "policy"},
    "ai4rag": {"key": "ai4rag", "repo_org": "IBM", "repo_name": "ai4rag"},
    "nested": {"key": "nested", "repo_org": "group/subgroup", "repo_name": "project"},
    "broken": {"key": "broken", "repo_org": "", "repo_name": "x"},
}


def _snapshot(tmp_path, mapping=None, components=None):
    """Write a manifest plus snapshot files the way sources.py lays them out."""
    root = tmp_path / "sources"
    arch = root / "architecture" / MAP_PATH
    arch.parent.mkdir(parents=True)
    arch.write_text(json.dumps({"meta": {}, "components": components or COMPONENTS}))
    sources = [{"id": "architecture", "role": "architecture",
                "url": "https://github.com/example/arch", "ref": "main",
                "sha": "a" * 40, "paths": ["architecture/v1"],
                "snapshot": "architecture", "files": 1, "content_hash": "x"}]
    if mapping is not None:
        target = root / "mapping" / MAPPING_PATH
        target.parent.mkdir(parents=True)
        target.write_text(yaml.safe_dump(mapping))
        sources.append({"id": "mapping", "role": "mapping", "url": "self",
                        "ref": "HEAD", "sha": "b" * 40, "paths": [MAPPING_PATH],
                        "snapshot": "mapping", "files": 1, "content_hash": "y"})
    manifest = {"version": 1, "group": "test", "config_digest": "z",
                "repository_data": {"source": "architecture", "path": MAP_PATH,
                                    "host": "github.com"},
                "jira": {"repository_url_field": "customfield_10933"},
                "sources": sources}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return manifest, root


MAPPING = {"components": {"AI Core Dashboard": ["odh-dashboard"],
                          "Model Serving": ["kserve", "odh-dashboard"]}}


class TestCanonicalUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://github.com/red-hat-data-services/odh-dashboard",
         "https://github.com/red-hat-data-services/odh-dashboard"),
        ("https://GitHub.com/IBM/AI4RAG.git", "https://github.com/ibm/ai4rag"),
        ("https://github.com/org/name/", "https://github.com/org/name"),
        ("git@github.com:Org/Name.git", "https://github.com/org/name"),
        ("https://gitlab.cee.redhat.com/group/subgroup/project",
         "https://gitlab.cee.redhat.com/group/subgroup/project"),
    ])
    def test_normalizes_variations(self, url, expected):
        assert lookup.normalize_repo_url(url) == expected

    @pytest.mark.parametrize("url", [
        "https://github.com/org/name?x=1",
        "https://github.com/org/name#frag",
        "https://github.com/org/../../other",
        "https://github.com/org/./name",
        "https://github.com//name",
        "https://user:pass@github.com/org/name",
        "https://token@github.com/org/name",
        "https://github.com:8443/org/name",
        "http://github.com/org/name",
        "file:///etc/passwd",
        "https://github.com/name",
        "",
    ])
    def test_rejects_unnormalizable(self, url):
        with pytest.raises(ResolutionError):
            lookup.normalize_repo_url(url)

    def test_canonical_is_lowercase_and_supports_nested_namespace(self):
        assert lookup.canonical_repo_url("GitLab.com", "Group/SubGroup", "Proj") == \
            "https://gitlab.com/group/subgroup/proj"


class TestDetails:
    def test_resolves_keys_to_full_details(self, tmp_path):
        manifest, root = _snapshot(tmp_path)
        details = lookup.resolve_details(manifest, root,
                                         ["odh-dashboard", "praxis-policy", "ai4rag"])
        assert details == [
            {"key": "odh-dashboard", "namespace": "red-hat-data-services",
             "name": "odh-dashboard",
             "url": "https://github.com/red-hat-data-services/odh-dashboard"},
            {"key": "praxis-policy", "namespace": "praxis-proxy", "name": "policy",
             "url": "https://github.com/praxis-proxy/policy"},
            {"key": "ai4rag", "namespace": "ibm", "name": "ai4rag",
             "url": "https://github.com/ibm/ai4rag"},
        ]

    def test_nested_namespace(self, tmp_path):
        manifest, root = _snapshot(tmp_path)
        [detail] = lookup.resolve_details(manifest, root, ["nested"])
        assert detail["url"] == "https://github.com/group/subgroup/project"

    def test_unknown_key_is_hard_failure(self, tmp_path):
        manifest, root = _snapshot(tmp_path)
        with pytest.raises(ResolutionError, match="not in the repository data"):
            lookup.resolve_details(manifest, root, ["odh-dashboard", "nope"])

    def test_entry_without_namespace_is_hard_failure(self, tmp_path):
        manifest, root = _snapshot(tmp_path)
        with pytest.raises(ResolutionError, match="no namespace path or name"):
            lookup.resolve_details(manifest, root, ["broken"])

    def test_missing_repository_data_is_hard_failure(self, tmp_path):
        manifest, root = _snapshot(tmp_path)
        (root / "architecture" / MAP_PATH).unlink()
        with pytest.raises(ResolutionError, match="cannot read repository data"):
            lookup.resolve_details(manifest, root, ["odh-dashboard"])


class TestPhase1:
    def test_no_components_skips(self, tmp_path):
        manifest, root = _snapshot(tmp_path, mapping=MAPPING)
        assert lookup.phase1_hints(manifest, root, []) == {"skipped": "no-components"}

    def test_no_mapping_skips(self, tmp_path):
        manifest, root = _snapshot(tmp_path)
        assert lookup.phase1_hints(manifest, root, ["AI Core Dashboard"]) == \
            {"skipped": "no-mapping"}

    def test_union_of_mapped_keys(self, tmp_path):
        manifest, root = _snapshot(tmp_path, mapping=MAPPING)
        result = lookup.phase1_hints(manifest, root,
                                     ["AI Core Dashboard", "Model Serving"])
        assert result == {"hints": ["kserve", "odh-dashboard"],
                          "unmapped_components": []}

    def test_unmapped_component_gives_no_hints(self, tmp_path):
        manifest, root = _snapshot(tmp_path, mapping=MAPPING)
        assert lookup.phase1_hints(manifest, root, ["Unknown Team"]) == \
            {"hints": [], "unmapped_components": ["Unknown Team"]}

    def test_stale_mapping_key_is_hard_failure(self, tmp_path):
        stale = {"components": {"AI Core Dashboard": ["odh-dashboard", "gone-repo"]}}
        manifest, root = _snapshot(tmp_path, mapping=stale)
        with pytest.raises(ResolutionError, match="gone-repo"):
            lookup.phase1_hints(manifest, root, ["AI Core Dashboard"])

    @pytest.mark.parametrize("bad", [
        {"other": {}},
        {"components": {"AI Core Dashboard": []}},
        {"components": {"AI Core Dashboard": "odh-dashboard"}},
        ["not", "a", "table"],
    ])
    def test_malformed_mapping_is_hard_failure(self, tmp_path, bad):
        manifest, root = _snapshot(tmp_path, mapping=bad)
        with pytest.raises(ResolutionError):
            lookup.phase1_hints(manifest, root, ["AI Core Dashboard"])


class TestCli:
    def test_hints_and_details(self, tmp_path, capsys):
        _, root = _snapshot(tmp_path, mapping=MAPPING)
        request = tmp_path / "request.json"
        request.write_text(json.dumps({"description": "Add a dashboard page",
                                       "components": ["AI Core Dashboard"]}))
        assert lookup.main(["hints", "--manifest", str(root / "manifest.json"),
                            "--request", str(request)]) == 0
        assert json.loads(capsys.readouterr().out)["hints"] == ["odh-dashboard"]

        assert lookup.main(["details", "--manifest", str(root / "manifest.json"),
                            "--keys", "kserve"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["url"] == \
            "https://github.com/red-hat-data-services/kserve"

    def test_unknown_key_exits_nonzero(self, tmp_path, capsys):
        _, root = _snapshot(tmp_path)
        assert lookup.main(["details", "--manifest", str(root / "manifest.json"),
                            "--keys", "nope"]) == 1
        assert "not an unresolved result" in capsys.readouterr().err

    def test_bad_request_exits_nonzero(self, tmp_path, capsys):
        _, root = _snapshot(tmp_path)
        request = tmp_path / "request.json"
        request.write_text(json.dumps({"components": ["x"]}))
        assert lookup.main(["hints", "--manifest", str(root / "manifest.json"),
                            "--request", str(request)]) == 1
