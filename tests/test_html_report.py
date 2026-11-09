#!/usr/bin/env python3
"""Tests for scripts/generate_html_report.py — HTML report generation."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_html_report.py")
FM_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "frontmatter.py")


def _write(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _run_fm(*args):
    result = subprocess.run(
        ["python3", FM_SCRIPT, *args],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"frontmatter.py failed: {result.stderr}"


def _run_report(*args):
    result = subprocess.run(
        ["python3", SCRIPT, *args],
        capture_output=True, text=True,
    )
    return result.stdout.strip(), result.stderr, result.returncode


def _setup_strategy(strat_id="RHAISTRAT-9999"):
    """Create minimal strategy, decomposition, review, and epic artifacts."""
    # Strategy file
    _write(f"artifacts/strat-tasks/{strat_id}.md",
           f"---\nstrat_id: {strat_id}\ntitle: Test Strategy\n---\n\n"
           f"A test strategy for report generation.\n")

    # Decomposition summary
    _write(f"artifacts/epic-tasks/{strat_id}-decomposition.md",
           f"## Epic List\n\n| ID | Title |\n|---|---|\n"
           f"| {strat_id}-E001 | Test Epic |\n\n"
           f"```mermaid\ngraph LR\n  E001\n```\n")
    _run_fm("set", f"artifacts/epic-tasks/{strat_id}-decomposition.md",
            f"parent_strat={strat_id}", "epic_count=1",
            "critical_path_length=1")

    # Review
    _write(f"artifacts/epic-reviews/{strat_id}-decomp-review.md",
           "## Review\n\nNo major issues.\n")
    _run_fm("set", f"artifacts/epic-reviews/{strat_id}-decomp-review.md",
            f"strat_id={strat_id}", "score=12", "pass=true",
            "recommendation=accept", "issues=[]")

    # Epic file
    _write(f"artifacts/epic-tasks/{strat_id}-E001.md",
           "## Title\n\nTest Epic One\n\n## Description\n\nA test epic.\n\n"
           "## Scope\n\n- Change A\n- Change B\n")
    _run_fm("set", f"artifacts/epic-tasks/{strat_id}-E001.md",
            f"epic_id={strat_id}-E001", "title=Test Epic One",
            f"parent_strat={strat_id}",
            "component=test-component", "team=Test Team",
            "type=Implementation", "priority=P0",
            "ai_signals.change_specificity=1",
            "ai_signals.pattern_precedent=1",
            "ai_signals.adapter_pattern=0",
            "ai_signals.existing_foundation=1",
            "ai_signals.open_questions=0",
            "ai_signals.external_dependency=0",
            "ai_signals.human_process_gates=0",
            "ai_signals.repo_access=1",
            "ai_signals.architecture_claims=0")


@pytest.fixture
def tmp_dir(tmp_path):
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(orig)


def test_malformed_review_renders_as_error(tmp_dir):
    from generate_html_report import render_strategy_section
    _setup_strategy()
    _write("artifacts/epic-reviews/RHAISTRAT-9999-decomp-review.md",
           "---\nscore: [broken\n---\n")
    _, _, stats = render_strategy_section("RHAISTRAT-9999")
    assert stats["error"] is True
    assert stats["passed"] is False


class TestHTMLReportGeneration:
    """Basic HTML report generation tests."""

    def test_generates_html_file(self, tmp_dir):
        _setup_strategy()
        out, err, rc = _run_report(
            "--start-time", "2026-01-01T00:00:00Z",
            "--output", "test-report.html",
            "RHAISTRAT-9999",
        )
        assert rc == 0, f"Script failed: {err}"
        assert os.path.exists("test-report.html")

        with open("test-report.html") as f:
            html = f.read()
        assert "<!DOCTYPE html>" in html
        assert "RHAISTRAT-9999" in html

    def test_default_output_path(self, tmp_dir):
        _setup_strategy()
        out, err, rc = _run_report(
            "--start-time", "2026-01-01T00:00:00Z",
            "RHAISTRAT-9999",
        )
        assert rc == 0, f"Script failed: {err}"
        expected = "artifacts/decompose-runs/2026-01-01T00-00-00Z-report.html"
        assert out == expected
        assert os.path.exists(expected)

    def test_auto_discovers_strategies(self, tmp_dir):
        _setup_strategy("RHAISTRAT-1001")
        _setup_strategy("RHAISTRAT-1002")
        out, err, rc = _run_report(
            "--start-time", "2026-01-01T00:00:00Z",
            "--output", "test-report.html",
        )
        assert rc == 0, f"Script failed: {err}"

        with open("test-report.html") as f:
            html = f.read()
        assert "RHAISTRAT-1001" in html
        assert "RHAISTRAT-1002" in html
        assert "2 strategies" in html

    def test_no_strategies_exits_nonzero(self, tmp_dir):
        os.makedirs("artifacts/epic-tasks", exist_ok=True)
        _, err, rc = _run_report(
            "--start-time", "2026-01-01T00:00:00Z",
            "--output", "test-report.html",
        )
        assert rc != 0
        assert "No strategies found" in err


class TestHTMLReportContent:
    """Verify report content details."""

    def test_contains_epic_card(self, tmp_dir):
        _setup_strategy()
        _run_report(
            "--start-time", "2026-01-01T00:00:00Z",
            "--output", "test-report.html",
            "RHAISTRAT-9999",
        )
        with open("test-report.html") as f:
            html = f.read()

        assert "RHAISTRAT-9999-E001" in html
        assert "Test Team" in html
        assert "test-component" in html

    def test_contains_review_score(self, tmp_dir):
        _setup_strategy()
        _run_report(
            "--start-time", "2026-01-01T00:00:00Z",
            "--output", "test-report.html",
            "RHAISTRAT-9999",
        )
        with open("test-report.html") as f:
            html = f.read()

        assert "12/14" in html
        assert "Pass" in html

    def test_contains_mermaid_diagram(self, tmp_dir):
        _setup_strategy()
        _run_report(
            "--start-time", "2026-01-01T00:00:00Z",
            "--output", "test-report.html",
            "RHAISTRAT-9999",
        )
        with open("test-report.html") as f:
            html = f.read()

        assert "mermaid" in html
        assert "graph LR" in html

    def test_contains_ai_signals(self, tmp_dir):
        _setup_strategy()
        _run_report(
            "--start-time", "2026-01-01T00:00:00Z",
            "--output", "test-report.html",
            "RHAISTRAT-9999",
        )
        with open("test-report.html") as f:
            html = f.read()

        assert "change_specificity" in html
        assert "pattern_precedent" in html

    def test_html_escapes_special_chars(self, tmp_dir):
        _setup_strategy()
        # Overwrite strategy with special chars in title
        _write("artifacts/strat-tasks/RHAISTRAT-9999.md",
               '---\nstrat_id: RHAISTRAT-9999\n'
               'title: \'Strategy with <script>alert("xss")</script>\'\n'
               '---\n\nBody content.\n')

        _run_report(
            "--start-time", "2026-01-01T00:00:00Z",
            "--output", "test-report.html",
            "RHAISTRAT-9999",
        )
        with open("test-report.html") as f:
            html = f.read()

        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html


class TestHTMLReportWithReviewIssues:
    """Verify review issues render correctly."""

    def test_renders_issues(self, tmp_dir):
        _setup_strategy()
        # Update review with issues
        issues_json = json.dumps([
            {"severity": "minor", "criterion": "DAG Coherence",
             "description": "Missing edge justification"},
            {"severity": "major", "criterion": "HLR Coverage",
             "description": "P1 HLR not mapped"},
        ])
        _run_fm("set", "artifacts/epic-reviews/RHAISTRAT-9999-decomp-review.md",
                "score=8", "pass=false",
                f"issues={issues_json}")

        _run_report(
            "--start-time", "2026-01-01T00:00:00Z",
            "--output", "test-report.html",
            "RHAISTRAT-9999",
        )
        with open("test-report.html") as f:
            html = f.read()

        assert "DAG Coherence" in html
        assert "HLR Coverage" in html
        assert "2 issues" in html


class TestHTMLReportBranchFiles:
    """Verify BRANCH file handling for conditional decompositions."""

    def test_includes_branch_epics(self, tmp_dir):
        _setup_strategy()

        # Add a BRANCH epic
        _write("artifacts/epic-tasks/RHAISTRAT-9999-BRANCH-A-E002.md",
               "## Title\n\nBranch A Epic\n\n## Description\n\n"
               "Conditional epic for branch A.\n")
        _run_fm("set", "artifacts/epic-tasks/RHAISTRAT-9999-BRANCH-A-E002.md",
                "epic_id=RHAISTRAT-9999-BRANCH-A-E002",
                "title=Branch A Epic",
                "parent_strat=RHAISTRAT-9999",
                "component=test-component", "team=Test Team",
                "type=Implementation", "priority=P1",
                "dependencies=RHAISTRAT-9999-E001",
                "gated_by=E001")

        _run_report(
            "--start-time", "2026-01-01T00:00:00Z",
            "--output", "test-report.html",
            "RHAISTRAT-9999",
        )
        with open("test-report.html") as f:
            html = f.read()

        assert "RHAISTRAT-9999-BRANCH-A-E002" in html
        assert "Branch A Epic" in html


class TestSignalSetSelection:
    """render_epic_card renders the signal set matching the epic type."""

    def test_investigation_epic_renders_investigation_signals(self):
        from generate_html_report import render_epic_card
        fm = {"epic_id": "RHAISTRAT-1-E001", "type": "Investigation",
              "ai_implementability": "High", "ai_implementability_score": 3,
              "investigation_signals": {
                  "question_specificity": 1, "source_accessibility": 1,
                  "local_runnability": 1, "cluster_hardware_dependence": 0,
                  "human_judgment_required": 0}}
        html, _, _ = render_epic_card(fm, "## Title\n\nT\n")
        assert "question_specificity" in html
        assert "human_judgment_required" in html
        assert "change_specificity" not in html  # not the 9-signal set

    def test_implementation_epic_renders_ai_signals(self):
        from generate_html_report import render_epic_card
        fm = {"epic_id": "RHAISTRAT-1-E002", "type": "Implementation",
              "ai_implementability": "High", "ai_implementability_score": 3,
              "ai_signals": {"change_specificity": 1}}
        html, _, _ = render_epic_card(fm, "## Title\n\nT\n")
        assert "change_specificity" in html
        assert "question_specificity" not in html
