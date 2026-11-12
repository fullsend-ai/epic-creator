#!/usr/bin/env python3
"""Tests for scripts/check_components.py — pre-submission component check."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from check_components import (
    STATUS_CONFLICT,
    STATUS_FLAGGED,
    STATUS_INVALID,
    STATUS_VALID,
    check_component,
    check_strategy,
    suggest_components,
)

PYTHON = sys.executable
SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
SCRIPT = os.path.join(SCRIPTS, "check_components.py")
FM_SCRIPT = os.path.join(SCRIPTS, "frontmatter.py")

VALID = {"MLflow", "vLLM", "Documentation", "Model Serving Runtimes",
         "AI Pipelines"}
STRAT = "RHAISTRAT-9999"


@pytest.fixture
def tmp_dir(tmp_path):
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(orig)


def _write_components(names=VALID):
    os.makedirs(".context", exist_ok=True)
    with open(".context/rhai-components.txt", "w") as f:
        f.write("".join(f"{n}\n" for n in sorted(names)))


def _write_epic(name, component, *extra):
    path = f"artifacts/epic-tasks/{name}.md"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("## Title\n\nEpic\n")
    result = subprocess.run(
        [PYTHON, FM_SCRIPT, "set", path, f"epic_id={name}",
         "title=Epic", f"parent_strat={STRAT}", f"component={component}",
         "team=Team", "type=Implementation", "priority=P1", *extra],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return path


def _run(*args):
    return subprocess.run([PYTHON, SCRIPT, *args],
                          capture_output=True, text=True)


class TestSuggestComponents:
    def test_parenthetical_suffix_suggests_base_name(self):
        assert suggest_components("MLflow (AI Gateway)", VALID)[0] == "MLflow"

    def test_case_mismatch_suggests_canonical_case(self):
        assert suggest_components("mlflow", VALID)[0] == "MLflow"

    def test_typo_suggests_close_name(self):
        assert "Model Serving Runtimes" in suggest_components(
            "Model Serving Runtime", VALID)

    def test_unrelated_name_has_no_suggestions(self):
        assert suggest_components("Quantum Billing Portal", VALID) == []

    def test_limit_respected(self):
        many = {f"Widget {i}" for i in range(10)}
        assert len(suggest_components("Widget", many)) <= 3


class TestCheckComponent:
    def test_valid_name(self):
        assert check_component("MLflow", False, VALID) == (STATUS_VALID, [])

    def test_invalid_name_with_suggestion(self):
        status, suggestions = check_component(
            "MLflow (AI Gateway)", False, VALID)
        assert status == STATUS_INVALID
        assert suggestions[0] == "MLflow"

    def test_no_suitable_component_flagged_is_not_error(self):
        assert check_component("Quantum Billing Portal", True, VALID) == \
            (STATUS_FLAGGED, [])

    def test_flag_with_canonical_name_conflicts(self):
        assert check_component("MLflow", True, VALID) == (STATUS_CONFLICT, [])

    def test_missing_component_is_invalid(self):
        assert check_component(None, False, VALID)[0] == STATUS_INVALID


class TestCheckStrategy:
    def test_includes_branch_files(self, tmp_dir):
        _write_epic(f"{STRAT}-E001", "MLflow")
        _write_epic(f"{STRAT}-BRANCH-A-E002", "vLLM (CUDA)")
        _write_epic("RHAISTRAT-1-E001", "Unrelated")
        results = check_strategy("artifacts", STRAT, VALID)
        assert [(r["epic_id"], r["status"]) for r in results] == [
            (f"{STRAT}-BRANCH-A-E002", STATUS_INVALID),
            (f"{STRAT}-E001", STATUS_VALID),
        ]


class TestCli:
    def test_all_valid_exits_zero(self, tmp_dir):
        _write_components()
        _write_epic(f"{STRAT}-E001", "MLflow")
        _write_epic(f"{STRAT}-E002", "vLLM")
        result = _run(STRAT)
        assert result.returncode == 0, result.stdout
        assert "2 valid, 0 invalid" in result.stdout

    def test_invalid_name_exits_nonzero_with_fix(self, tmp_dir):
        _write_components()
        path = _write_epic(f"{STRAT}-E001", "MLflow (AI Gateway)")
        result = _run(STRAT)
        assert result.returncode == 1
        assert f"INVALID {STRAT}-E001" in result.stdout
        assert "'MLflow'" in result.stdout
        assert f'frontmatter.py set {path} component="MLflow"' \
            in result.stdout
        assert "needs_component=true" in result.stdout

    def test_needs_component_flag_exits_zero(self, tmp_dir):
        _write_components()
        _write_epic(f"{STRAT}-E001", "Quantum Billing Portal",
                    "needs_component=true")
        result = _run(STRAT)
        assert result.returncode == 0, result.stdout
        assert f"FLAGGED {STRAT}-E001" in result.stdout
        assert "1 flagged for human review" in result.stdout

    def test_conflicting_flag_exits_nonzero(self, tmp_dir):
        _write_components()
        _write_epic(f"{STRAT}-E001", "MLflow", "needs_component=true")
        result = _run(STRAT)
        assert result.returncode == 1
        assert f"CONFLICT {STRAT}-E001" in result.stdout

    def test_missing_components_file_skips(self, tmp_dir):
        _write_epic(f"{STRAT}-E001", "Anything")
        result = _run(STRAT)
        assert result.returncode == 0
        assert "skipped" in result.stderr
