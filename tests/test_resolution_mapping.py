#!/usr/bin/env python3
"""The shipped RHAI component mapping and the four Phase 1 scenarios (ADR-0176)."""
import json
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "repository-resolution" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import verify  # noqa: E402
from test_resolution_lookup import _snapshot  # noqa: E402
from test_resolution_verify import DASHBOARD_DOC, DASHBOARD_TEXT  # noqa: E402

MAPPING_FILE = ROOT / "config" / "rhai" / "component-repositories.yaml"
SCHEMA = ROOT / "config" / "_schema" / "component-repositories.schema.json"
QUOTE = "The ODH Dashboard is the web UI for OpenShift AI"


class TestShippedMapping:
    def test_matches_schema(self):
        data = yaml.safe_load(MAPPING_FILE.read_text())
        jsonschema.validate(data, json.loads(SCHEMA.read_text()))

    def test_is_declared_as_mapping_source(self):
        config = yaml.safe_load((ROOT / "config" / "resolver.yaml").read_text())
        [mapping] = [s for s in config["sources"] if s["role"] == "mapping"]
        assert mapping["url"] == "self" and mapping["ref"] == "HEAD"
        assert mapping["paths"] == ["config/rhai/component-repositories.yaml"]

    def test_records_provenance(self):
        text = MAPPING_FILE.read_text()
        assert "docs.google.com" in text and "component-map.json" in text


def _resolved(hint, overridden=()):
    return {"status": "resolved",
            "phase1": None,
            "repositories": [{
                "key": "odh-dashboard", "namespace": "red-hat-data-services",
                "name": "odh-dashboard",
                "url": "https://github.com/red-hat-data-services/odh-dashboard",
                "hint": hint,
                "evidence": [{"source": "architecture", "path": DASHBOARD_DOC,
                              "quote": QUOTE}]}],
            "overridden_hints": [{"key": k, "reason": "UI-only work"}
                                 for k in overridden]}


@pytest.fixture
def run(tmp_path):
    def make(mapping):
        manifest, root = _snapshot(tmp_path / f"m{len(list(tmp_path.iterdir()))}",
                                   mapping=mapping)
        (root / "architecture" / DASHBOARD_DOC).write_text(DASHBOARD_TEXT)
        return manifest, root
    return make


class TestFourScenarios:
    """Plan Part C: the mapping's four cases, end to end through verify."""

    def test_component_with_correct_mapping_is_confirmed(self, run):
        manifest, root = run({"components": {"AI Core Dashboard": ["odh-dashboard"]}})
        result = _resolved("confirmed")
        result["phase1"] = {"hints": ["odh-dashboard"]}
        request = {"description": "x", "components": ["AI Core Dashboard"]}
        assert verify.verify(manifest, root, request, result) == []

    def test_component_with_wrong_mapping_is_overridden(self, run):
        manifest, root = run({"components": {"AI Core Dashboard": ["kserve"]}})
        result = _resolved("none", overridden=["kserve"])
        result["phase1"] = {"hints": ["kserve"]}
        request = {"description": "x", "components": ["AI Core Dashboard"]}
        assert verify.verify(manifest, root, request, result) == []

    def test_component_without_mapping_entry_resolves_without_hints(self, run):
        manifest, root = run({"components": {"Model Serving": ["kserve"]}})
        result = _resolved("none")
        result["phase1"] = {"hints": []}
        request = {"description": "x", "components": ["AI Core Dashboard"]}
        assert verify.verify(manifest, root, request, result) == []

    def test_no_component_skips_phase1(self, run):
        manifest, root = run({"components": {"AI Core Dashboard": ["odh-dashboard"]}})
        result = _resolved("none")
        result["phase1"] = {"skipped": "no-components"}
        request = {"description": "x", "components": []}
        assert verify.verify(manifest, root, request, result) == []
