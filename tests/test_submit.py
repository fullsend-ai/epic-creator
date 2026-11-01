#!/usr/bin/env python3
"""Tests for scripts/submit.py — epic submission to Jira."""
import json
import os
import subprocess
import sys
import urllib.error
from http.client import BadStatusLine, IncompleteRead
from unittest.mock import MagicMock, patch

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from submit import (
    _attach_branch_plans,
    _attach_frontmatter,
    _build_branch_plan_md,
    _build_description,
    _build_plan,
    _create_epics,
    _get_strat_assignee,
    _scan_branch_epics,
    _scan_epics,
    _check_review_passed,
    _find_submittable_strats,
    validate_component,
    _load_valid_components,
    PRIORITY_MAP,
)
from artifact_utils import read_frontmatter
import submit as submit_module

PYTHON = sys.executable
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "submit.py")
FM_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                         "frontmatter.py")


def _write(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _run_fm(*args):
    result = subprocess.run(
        [PYTHON, FM_SCRIPT, *args],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"frontmatter.py failed: {result.stderr}"


def _run_submit(*args):
    """Use a temporary component file."""
    _write(".context/rhai-components.txt", "MLflow\nvLLM\nDocumentation\n")
    result = subprocess.run(
        [PYTHON, SCRIPT, *args],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"submit.py failed: {result.stderr}"
    return result


def _setup_strategy(strat_id="RHAISTRAT-9999", epic_count=2, review_pass=True,
                    review_score=12):
    """Create minimal strategy, epic, decomposition, and review artifacts."""
    # Decomposition summary
    _write(f"artifacts/epic-tasks/{strat_id}-decomposition.md",
           f"## Epic List\n\n| ID | Title |\n|---|---|\n")
    _run_fm("set", f"artifacts/epic-tasks/{strat_id}-decomposition.md",
            f"parent_strat={strat_id}", f"epic_count={epic_count}",
            "critical_path_length=1")

    # Review
    _write(f"artifacts/epic-reviews/{strat_id}-decomp-review.md",
           "## Review\n\nNo issues.\n")
    rec = "accept" if review_pass else "revise"
    _run_fm("set", f"artifacts/epic-reviews/{strat_id}-decomp-review.md",
            f"strat_id={strat_id}", f"score={review_score}",
            f"pass={'true' if review_pass else 'false'}",
            f"recommendation={rec}", "issues=[]")

    # Epic files
    for i in range(1, epic_count + 1):
        eid = f"{strat_id}-E{i:03d}"
        deps = f"dependencies={strat_id}-E{i-1:03d}" if i > 1 else ""
        body = (f"## Title\n\nTest Epic {i}\n\n"
                f"## Description\n\nEpic {i} for testing.\n\n"
                f"## Scope\n\n- Change A\n")
        _write(f"artifacts/epic-tasks/{eid}.md", body)
        fm_args = [
            "set", f"artifacts/epic-tasks/{eid}.md",
            f"epic_id={eid}", f"title=Test Epic {i}",
            f"parent_strat={strat_id}",
            "component=test-component", "team=Test Team",
            "type=Implementation", f"priority=P{min(i-1, 2)}",
        ]
        if deps:
            fm_args.append(deps)
        _run_fm(*fm_args)


@pytest.fixture
def tmp_dir(tmp_path):
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(orig)


class TestPriorityMap:
    def test_p0_maps_to_critical(self):
        assert PRIORITY_MAP["P0"] == "Critical"

    def test_p1_maps_to_major(self):
        assert PRIORITY_MAP["P1"] == "Major"

    def test_p2_maps_to_minor(self):
        assert PRIORITY_MAP["P2"] == "Minor"


class TestComponentValidation:
    def test_valid_component_passes(self):
        components = {"MLflow", "vLLM", "Documentation"}
        assert validate_component("MLflow", components) == "MLflow"

    def test_invalid_component_returns_none(self):
        components = {"MLflow", "vLLM", "Documentation"}
        assert validate_component("MLflow (AI Gateway)", components) is None

    def test_empty_component_returns_none(self):
        components = {"MLflow", "vLLM"}
        assert validate_component("", components) is None

    def test_empty_valid_set_returns_none(self):
        assert validate_component("MLflow", set()) is None

    def test_load_components_from_file(self, tmp_path):
        comp_file = tmp_path / "components.txt"
        comp_file.write_text("MLflow\nvLLM\nDocumentation\n")
        from unittest.mock import patch
        with patch("submit.COMPONENTS_PATH", str(comp_file)):
            result = _load_valid_components()
        assert result == {"MLflow", "vLLM", "Documentation"}

    def test_load_components_missing_file(self, tmp_path):
        from unittest.mock import patch
        with patch("submit.COMPONENTS_PATH", str(tmp_path / "nope.txt")):
            result = _load_valid_components()
        assert result is None


class TestBuildDescription:
    def test_strips_title_section(self):
        body = "## Title\n\nMy Title\n\n## Description\n\nContent here."
        result = _build_description(body)
        assert "My Title" not in result
        assert "## Description" in result
        assert "Content here." in result

    def test_preserves_other_sections(self):
        body = ("## Title\n\nT\n\n## Description\n\nD\n\n"
                "## Scope\n\n- Item\n")
        result = _build_description(body)
        assert "## Description" in result
        assert "## Scope" in result
        assert "- Item" in result


class TestScanEpics:
    def test_finds_epic_files(self, tmp_dir):
        _setup_strategy()
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        assert len(epics) == 2
        assert epics[0][1]["epic_id"] == "RHAISTRAT-9999-E001"
        assert epics[1][1]["epic_id"] == "RHAISTRAT-9999-E002"

    def test_returns_empty_for_missing_strat(self, tmp_dir):
        os.makedirs("artifacts/epic-tasks", exist_ok=True)
        assert _scan_epics("artifacts", "RHAISTRAT-0000") == []

    def test_skips_decomposition_files(self, tmp_dir):
        _setup_strategy()
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        epic_ids = [e[1]["epic_id"] for e in epics]
        assert not any("decomposition" in eid for eid in epic_ids)


class TestCheckReviewPassed:
    def test_passing_review(self, tmp_dir):
        _setup_strategy(review_pass=True, review_score=12)
        passed, data = _check_review_passed("artifacts", "RHAISTRAT-9999")
        assert passed is True
        assert data["score"] == 12

    def test_failing_review(self, tmp_dir):
        _setup_strategy(review_pass=False, review_score=5)
        passed, data = _check_review_passed("artifacts", "RHAISTRAT-9999")
        assert passed is False

    def test_missing_review(self, tmp_dir):
        os.makedirs("artifacts/epic-reviews", exist_ok=True)
        passed, data = _check_review_passed("artifacts", "RHAISTRAT-0000")
        assert passed is False
        assert data is None


class TestFindSubmittableStrats:
    def test_finds_strategies_with_decompositions(self, tmp_dir):
        _setup_strategy("RHAISTRAT-1001")
        _setup_strategy("RHAISTRAT-1002")
        strats = _find_submittable_strats("artifacts")
        assert "RHAISTRAT-1001" in strats
        assert "RHAISTRAT-1002" in strats

    def test_returns_empty_without_decompositions(self, tmp_dir):
        os.makedirs("artifacts/epic-tasks", exist_ok=True)
        assert _find_submittable_strats("artifacts") == []


class TestLabels:
    """Verify label generation in _build_plan."""

    def test_all_labels_prefixed(self, tmp_dir):
        _setup_strategy(epic_count=1)
        # Set optional fields that generate labels
        _run_fm("set", "artifacts/epic-tasks/RHAISTRAT-9999-E001.md",
                "type=Investigation",
                "implementation_type=docs-authoring",
                "ai_implementability=High",
                "ai_implementability_score=7")
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        for label in plan[0]["labels"]:
            assert label.startswith("epic-creator-"), \
                f"Label {label!r} missing epic-creator- prefix"

    def test_investigation_label(self, tmp_dir):
        _setup_strategy(epic_count=1)
        _run_fm("set", "artifacts/epic-tasks/RHAISTRAT-9999-E001.md",
                "type=Investigation")
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        assert "epic-creator-investigation" in plan[0]["labels"]

    def test_impl_type_label(self, tmp_dir):
        _setup_strategy(epic_count=1)
        _run_fm("set", "artifacts/epic-tasks/RHAISTRAT-9999-E001.md",
                "implementation_type=docs-authoring")
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        assert "epic-creator-impl-docs-authoring" in plan[0]["labels"]

    def test_ai_impl_label(self, tmp_dir):
        _setup_strategy(epic_count=1)
        _run_fm("set", "artifacts/epic-tasks/RHAISTRAT-9999-E001.md",
                "ai_implementability=High",
                "ai_implementability_score=7")
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        assert "epic-creator-ai-impl-high" in plan[0]["labels"]

    def test_no_ai_impl_label_when_absent(self, tmp_dir):
        _setup_strategy(epic_count=1)
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        assert not any("ai-impl" in label for label in plan[0]["labels"])

    def test_needs_component_label(self, tmp_dir):
        _setup_strategy(epic_count=1)
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        # No valid components → needs-component flag
        plan = _build_plan(epics, {"SomeOtherComponent"})
        assert "epic-creator-needs-component" in plan[0]["labels"]

    def test_valid_component_no_needs_label(self, tmp_dir):
        _setup_strategy(epic_count=1)
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, {"test-component"})
        assert "epic-creator-needs-component" not in plan[0]["labels"]

    def test_no_needs_component_when_cache_unavailable(self, tmp_dir):
        """When component cache is None (missing file), pass through
        the frontmatter component without adding needs-component."""
        _setup_strategy(epic_count=1)
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, None)
        assert "epic-creator-needs-component" not in plan[0]["labels"]
        assert plan[0]["component"] == "test-component"


class TestBuildPlan:
    def test_includes_jira_key_from_frontmatter(self, tmp_dir):
        _setup_strategy()
        # Set jira_key on first epic
        _run_fm("set", "artifacts/epic-tasks/RHAISTRAT-9999-E001.md",
                "jira_key=RHAI-100")
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        assert plan[0]["jira_key"] == "RHAI-100"
        assert plan[1]["jira_key"] is None

    def test_plan_without_jira_keys(self, tmp_dir):
        _setup_strategy()
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        assert all(e["jira_key"] is None for e in plan)


class TestCreateEpics:
    """Test _create_epics with mocked Jira API."""

    def test_skips_already_created(self, tmp_dir):
        from unittest.mock import patch, MagicMock
        _setup_strategy()
        _run_fm("set", "artifacts/epic-tasks/RHAISTRAT-9999-E001.md",
                "jira_key=RHAI-100")
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())

        call_count = 0

        def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return "RHAI-200"

        with patch("submit.create_issue", side_effect=mock_create), \
             patch("submit.markdown_to_adf", return_value={}):
            id_map, errors = _create_epics("s", "u", "t", plan, "RHAISTRAT-9999")

        # Only E002 should be created (E001 already has jira_key)
        assert call_count == 1
        assert errors == 0
        assert id_map["RHAISTRAT-9999-E001"] == "RHAI-100"
        assert id_map["RHAISTRAT-9999-E002"] == "RHAI-200"

    def test_writes_jira_key_to_frontmatter(self, tmp_dir):
        from unittest.mock import patch
        _setup_strategy(epic_count=1)
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())

        with patch("submit.create_issue", return_value="RHAI-500"), \
             patch("submit.markdown_to_adf", return_value={}):
            _create_epics("s", "u", "t", plan, "RHAISTRAT-9999")

        # Verify jira_key persisted in frontmatter
        data, _ = read_frontmatter(
            "artifacts/epic-tasks/RHAISTRAT-9999-E001.md")
        assert data["jira_key"] == "RHAI-500"

    def test_stops_on_first_failure(self, tmp_dir):
        from unittest.mock import patch
        _setup_strategy(epic_count=3)
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())

        call_count = 0

        def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("API error")
            return f"RHAI-{100 + call_count}"

        with patch("submit.create_issue", side_effect=mock_create), \
             patch("submit.markdown_to_adf", return_value={}):
            id_map, errors = _create_epics("s", "u", "t", plan, "RHAISTRAT-9999")

        # Should have tried 2 (first succeeds, second fails, third skipped)
        assert call_count == 2
        assert errors == 1
        assert "RHAISTRAT-9999-E001" in id_map
        assert "RHAISTRAT-9999-E002" not in id_map
        assert "RHAISTRAT-9999-E003" not in id_map

    def test_resume_after_failure(self, tmp_dir):
        """Simulate re-run after a partial failure."""
        from unittest.mock import patch
        _setup_strategy(epic_count=2)

        # Simulate first run: E001 was created, E002 failed
        _run_fm("set", "artifacts/epic-tasks/RHAISTRAT-9999-E001.md",
                "jira_key=RHAI-100")

        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())

        with patch("submit.create_issue", return_value="RHAI-200"), \
             patch("submit.markdown_to_adf", return_value={}):
            id_map, errors = _create_epics("s", "u", "t", plan, "RHAISTRAT-9999")

        assert errors == 0
        assert id_map["RHAISTRAT-9999-E001"] == "RHAI-100"  # from prior run
        assert id_map["RHAISTRAT-9999-E002"] == "RHAI-200"  # newly created

        # Verify E002's jira_key is now in frontmatter
        data, _ = read_frontmatter(
            "artifacts/epic-tasks/RHAISTRAT-9999-E002.md")
        assert data["jira_key"] == "RHAI-200"


class TestAttachFrontmatter:
    """Verify frontmatter YAML is attached to Jira epics."""

    def test_attaches_yaml_for_each_epic(self, tmp_dir):
        from unittest.mock import patch, call
        _setup_strategy()
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        id_to_jira_key = {
            "RHAISTRAT-9999-E001": "RHAI-100",
            "RHAISTRAT-9999-E002": "RHAI-200",
        }

        with patch("submit.add_attachment") as mock_attach, \
             patch("submit._get_existing_attachments", return_value={}):
            errors = _attach_frontmatter("s", "u", "t", plan, id_to_jira_key)

        assert errors == 0
        assert mock_attach.call_count == 2
        filenames = [c.args[4] for c in mock_attach.call_args_list]
        assert "RHAI-100-frontmatter.yaml" in filenames
        assert "RHAI-200-frontmatter.yaml" in filenames

    def test_yaml_contains_frontmatter_fields(self, tmp_dir):
        import yaml as _yaml
        from unittest.mock import patch
        _setup_strategy(epic_count=1)
        _run_fm("set", "artifacts/epic-tasks/RHAISTRAT-9999-E001.md",
                "type=Investigation",
                "ai_implementability=High",
                "ai_implementability_score=7")
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        id_to_jira_key = {"RHAISTRAT-9999-E001": "RHAI-100"}

        captured_content = {}

        def capture_attach(_s, _u, _t, key, filename, content):
            captured_content[filename] = content

        with patch("submit.add_attachment", side_effect=capture_attach), \
             patch("submit._get_existing_attachments", return_value={}):
            _attach_frontmatter("s", "u", "t", plan, id_to_jira_key)

        yaml_str = captured_content["RHAI-100-frontmatter.yaml"]
        data = _yaml.safe_load(yaml_str)
        assert data["epic_id"] == "RHAISTRAT-9999-E001"
        assert data["type"] == "Investigation"
        assert data["ai_implementability"] == "High"

    def test_replaces_existing_attachment(self, tmp_dir):
        from unittest.mock import patch
        _setup_strategy(epic_count=1)
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        id_to_jira_key = {"RHAISTRAT-9999-E001": "RHAI-100"}

        with patch("submit.add_attachment") as mock_attach, \
             patch("submit.api_call") as mock_api, \
             patch("submit._get_existing_attachments",
                   return_value={"RHAI-100-frontmatter.yaml": "att-99"}):
            errors = _attach_frontmatter("s", "u", "t", plan, id_to_jira_key)

        assert errors == 0
        # Old attachment deleted, new one uploaded
        mock_api.assert_called_once_with(
            "s", "/attachment/att-99", "u", "t", method="DELETE")
        assert mock_attach.call_count == 1

    def test_skips_epics_not_in_jira(self, tmp_dir):
        from unittest.mock import patch
        _setup_strategy()
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        # Only E001 was created in Jira
        id_to_jira_key = {"RHAISTRAT-9999-E001": "RHAI-100"}

        with patch("submit.add_attachment") as mock_attach, \
             patch("submit._get_existing_attachments", return_value={}):
            errors = _attach_frontmatter("s", "u", "t", plan, id_to_jira_key)

        assert errors == 0
        assert mock_attach.call_count == 1

    def test_counts_errors_on_failure(self, tmp_dir):
        from unittest.mock import patch
        _setup_strategy()
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())
        id_to_jira_key = {
            "RHAISTRAT-9999-E001": "RHAI-100",
            "RHAISTRAT-9999-E002": "RHAI-200",
        }

        call_count = 0

        def fail_second(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("upload failed")

        with patch("submit.add_attachment", side_effect=fail_second), \
             patch("submit._get_existing_attachments", return_value={}):
            errors = _attach_frontmatter("s", "u", "t", plan, id_to_jira_key)

        assert errors == 1
        assert call_count == 2


def _setup_branch_epics(strat_id="RHAISTRAT-9999",
                        investigation_id="RHAISTRAT-9999-E001"):
    """Create branch epic artifact files for conditional decomposition."""
    for branch, count in [("A", 1), ("B", 2)]:
        for i in range(1, count + 1):
            eid = f"{strat_id}-BRANCH-{branch}-E{i + 2:03d}"
            body = (f"## Title\n\nBranch {branch} Epic {i}\n\n"
                    f"## Description\n\nConditional epic.\n")
            _write(f"artifacts/epic-tasks/{eid}.md", body)
            _run_fm("set", f"artifacts/epic-tasks/{eid}.md",
                    f"epic_id={eid}",
                    f"title=Branch {branch} Epic {i}",
                    f"parent_strat={strat_id}",
                    "component=test-component", "team=Test Team",
                    "type=Implementation", "priority=P1",
                    f"branch={branch}",
                    f"gated_by={investigation_id}")


class TestBranchPlans:
    """Verify conditional branch plan scanning and attachment."""

    def test_scan_finds_branch_files(self, tmp_dir):
        _setup_strategy()
        _setup_branch_epics()
        branches = _scan_branch_epics("artifacts", "RHAISTRAT-9999")
        assert len(branches) == 2
        assert ("A", "RHAISTRAT-9999-E001") in branches
        assert ("B", "RHAISTRAT-9999-E001") in branches
        assert len(branches[("A", "RHAISTRAT-9999-E001")]) == 1
        assert len(branches[("B", "RHAISTRAT-9999-E001")]) == 2

    def test_scan_returns_empty_without_branches(self, tmp_dir):
        _setup_strategy()
        branches = _scan_branch_epics("artifacts", "RHAISTRAT-9999")
        assert branches == {}

    def test_build_branch_plan_md_contains_frontmatter(self, tmp_dir):
        _setup_strategy()
        _setup_branch_epics()
        branches = _scan_branch_epics("artifacts", "RHAISTRAT-9999")
        epics = branches[("B", "RHAISTRAT-9999-E001")]
        md = _build_branch_plan_md(epics)
        assert "RHAISTRAT-9999-BRANCH-B-E003" in md
        assert "RHAISTRAT-9999-BRANCH-B-E004" in md
        assert "gated_by:" in md
        assert "## Description" in md

    def test_attach_to_investigation_epic(self, tmp_dir):
        from unittest.mock import patch
        _setup_strategy()
        _setup_branch_epics()
        id_to_jira_key = {"RHAISTRAT-9999-E001": "RHAI-100"}

        with patch("submit.add_attachment") as mock_attach, \
             patch("submit._get_existing_attachments", return_value={}):
            errors = _attach_branch_plans(
                "s", "u", "t", "artifacts", "RHAISTRAT-9999",
                id_to_jira_key)

        assert errors == 0
        assert mock_attach.call_count == 2
        calls = {c.args[4]: c.args[3] for c in mock_attach.call_args_list}
        assert "RHAI-100-branch-a-plan.md" in calls
        assert "RHAI-100-branch-b-plan.md" in calls
        # Both attached to the investigation epic
        assert calls["RHAI-100-branch-a-plan.md"] == "RHAI-100"
        assert calls["RHAI-100-branch-b-plan.md"] == "RHAI-100"

    def test_skips_when_investigation_not_in_jira(self, tmp_dir):
        from unittest.mock import patch
        _setup_strategy()
        _setup_branch_epics()
        # Investigation epic not in jira map
        id_to_jira_key = {"RHAISTRAT-9999-E002": "RHAI-200"}

        with patch("submit.add_attachment") as mock_attach, \
             patch("submit._get_existing_attachments", return_value={}):
            errors = _attach_branch_plans(
                "s", "u", "t", "artifacts", "RHAISTRAT-9999",
                id_to_jira_key)

        assert errors == 0
        assert mock_attach.call_count == 0

    def test_counts_errors_on_failure(self, tmp_dir):
        from unittest.mock import patch
        _setup_strategy()
        _setup_branch_epics()
        id_to_jira_key = {"RHAISTRAT-9999-E001": "RHAI-100"}

        with patch("submit.add_attachment",
                   side_effect=RuntimeError("upload failed")), \
             patch("submit._get_existing_attachments", return_value={}):
            errors = _attach_branch_plans(
                "s", "u", "t", "artifacts", "RHAISTRAT-9999",
                id_to_jira_key)

        assert errors == 2  # one per branch


class TestGetStratAssignee:
    """Test _get_strat_assignee fetches assignee from strategy issue."""

    def test_returns_account_id(self):
        from unittest.mock import patch
        mock_issue = {
            "fields": {
                "assignee": {
                    "accountId": "abc123",
                    "displayName": "Jane Doe",
                }
            }
        }
        with patch("submit.get_issue", return_value=mock_issue):
            result = _get_strat_assignee("s", "u", "t", "RHAISTRAT-100")
        assert result == "abc123"

    def test_returns_none_when_unassigned(self):
        from unittest.mock import patch
        mock_issue = {"fields": {"assignee": None}}
        with patch("submit.get_issue", return_value=mock_issue):
            result = _get_strat_assignee("s", "u", "t", "RHAISTRAT-100")
        assert result is None

    def test_returns_none_on_api_error(self):
        from unittest.mock import patch
        with patch("submit.get_issue", side_effect=RuntimeError("timeout")):
            result = _get_strat_assignee("s", "u", "t", "RHAISTRAT-100")
        assert result is None


class TestCreateEpicsAssignee:
    """Test that _create_epics passes assignee_id to create_issue."""

    def test_passes_assignee_id(self, tmp_dir):
        from unittest.mock import patch, call
        _setup_strategy(epic_count=1)
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())

        with patch("submit.create_issue", return_value="RHAI-500") as mock_ci, \
             patch("submit.markdown_to_adf", return_value={}):
            _create_epics("s", "u", "t", plan, "RHAISTRAT-9999",
                          assignee_id="user-abc")

        assert mock_ci.call_count == 1
        assert mock_ci.call_args.kwargs["assignee_id"] == "user-abc"

    def test_no_assignee_when_none(self, tmp_dir):
        from unittest.mock import patch
        _setup_strategy(epic_count=1)
        epics = _scan_epics("artifacts", "RHAISTRAT-9999")
        plan = _build_plan(epics, set())

        with patch("submit.create_issue", return_value="RHAI-500") as mock_ci, \
             patch("submit.markdown_to_adf", return_value={}):
            _create_epics("s", "u", "t", plan, "RHAISTRAT-9999")

        assert mock_ci.call_args.kwargs.get("assignee_id") is None


@pytest.fixture
def submission_boundaries(monkeypatch):
    """Keep real plan/creation logic, but stub every network and write boundary."""
    mutations = {}
    for name, result in (
        ("create_issue", "RHAI-9003"),
        ("_create_dependency_links", 0),
        ("_attach_frontmatter", 0),
        ("_attach_branch_plans", 0),
        ("_label_strategy", True),
    ):
        mutations[name] = MagicMock(return_value=result)
        monkeypatch.setattr(submit_module, name, mutations[name])

    strategy = {"key": "RHAISTRAT-9999", "fields": {
        "issuelinks": [], "assignee": None,
    }}
    get_issue = MagicMock(return_value=strategy)
    monkeypatch.setattr(submit_module, "get_issue", get_issue)

    def no_network(*args, **kwargs):
        raise AssertionError("Submission safety tests must not access the network")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    return mutations, get_issue


class TestSubmissionSafety:
    def _plan(self, epic_count=1):
        _setup_strategy(epic_count=epic_count)
        return _build_plan(_scan_epics("artifacts", "RHAISTRAT-9999"), set())

    def _assert_no_writes(self, boundaries):
        for mutation in boundaries[0].values():
            mutation.assert_not_called()

    @pytest.mark.parametrize("page", [
        {"issues": [{"key": "RHAISTRAT-9999"}, {"key": "RHOAIENG-9002"}],
         "isLast": True},
        {"issues": [], "isLast": True},
        {"issues": [{"key": "RHAISTRAT-9999"}], "isLast": False,
         "nextPageToken": "more-results"},
    ])
    def test_existing_or_inconclusive_work_blocks_all_writes(
            self, tmp_dir, submission_boundaries, page):
        plan = self._plan()
        with patch("jira_utils.api_call_with_retry", side_effect=[
            {"accountId": "test-account", "active": True}, page,
        ]):
            assert submit_module._submit_strategy(
                "s", "u", "t", "RHAISTRAT-9999", plan, False) == (0, 1)

        self._assert_no_writes(submission_boundaries)
        data, _ = read_frontmatter(plan[0]["path"])
        assert not data.get("jira_key")

    def test_failed_auth_blocks_even_partial_resume(
            self, tmp_dir, submission_boundaries, capsys):
        plan = self._plan(epic_count=2)
        plan[0]["jira_key"] = "RHAI-9002"
        failure = urllib.error.HTTPError("https://jira.example.com", 401,
                                         "Unauthorized", {}, None)
        with patch("jira_utils.api_call_with_retry", side_effect=failure):
            assert submit_module._submit_strategy(
                "s", "u", "t", "RHAISTRAT-9999", plan, False) == (0, 1)
        self._assert_no_writes(submission_boundaries)
        submission_boundaries[1].assert_not_called()
        output = capsys.readouterr().err
        assert "Submission blocked for RHAISTRAT-9999" in output
        assert "No Jira writes were made for RHAISTRAT-9999 in this attempt." in output
        assert "Preserve the original artifacts and recorded jira_key values" in output

    @pytest.mark.parametrize("answer", [
        None,
        {"key": "RHAISTRAT-9000", "fields": {"issuelinks": []}},
        {"key": "RHAISTRAT-9999", "fields": {}},
        urllib.error.HTTPError("https://jira.example.com", 403,
                               "Forbidden", {}, None),
        BadStatusLine("invalid status"),
        IncompleteRead(b"partial"),
    ])
    def test_unverified_strategy_blocks_all_writes(
            self, tmp_dir, submission_boundaries, answer):
        plan = self._plan()
        if isinstance(answer, Exception):
            submission_boundaries[1].side_effect = answer
        else:
            submission_boundaries[1].return_value = answer
        with patch("submit.verify_jira_auth"):
            assert submit_module._submit_strategy(
                "s", "u", "t", "RHAISTRAT-9999", plan, False) == (0, 1)
        self._assert_no_writes(submission_boundaries)

    def test_verified_fresh_plan_creates_epic_and_records_key(
            self, tmp_dir, submission_boundaries):
        plan = self._plan()
        with patch("jira_utils.api_call_with_retry", side_effect=[
            {"accountId": "test-account", "active": True},
            {"issues": [{"key": "RHAISTRAT-9999"}], "isLast": True},
        ]):
            assert submit_module._submit_strategy(
                "s", "u", "t", "RHAISTRAT-9999", plan, False) == (1, 0)

        submission_boundaries[0]["create_issue"].assert_called_once()
        data, _ = read_frontmatter(plan[0]["path"])
        assert data["jira_key"] == "RHAI-9003"

    @pytest.mark.parametrize("issue_type,created", [("Epic", 0), ("Outcome", 1)])
    def test_fresh_submission_uses_actual_type_of_incorporated_issue(
            self, tmp_dir, submission_boundaries, issue_type, created):
        plan = self._plan()
        submission_boundaries[1].return_value["fields"]["issuelinks"] = [{
            "type": {"name": "Incorporates"},
            "outwardIssue": {"key": "RHAI-9002", "fields": {
                "issuetype": {"name": issue_type},
            }},
        }]
        responses = [{"accountId": "test-account", "active": True}]
        if created:
            responses.append({"issues": [{"key": "RHAISTRAT-9999"}], "isLast": True})
        with patch("jira_utils.api_call_with_retry", side_effect=responses):
            result = submit_module._submit_strategy(
                "s", "u", "t", "RHAISTRAT-9999", plan, False)
        assert result == ((1, 0) if created else (0, 1))
        if created:
            submission_boundaries[0]["create_issue"].assert_called_once()
        else:
            self._assert_no_writes(submission_boundaries)

    def test_unresolved_incorporated_issue_blocks_fresh_submission(
            self, tmp_dir, submission_boundaries, capsys):
        plan = self._plan()
        submission_boundaries[1].return_value["fields"]["issuelinks"] = [{
            "type": {"name": "Incorporates"}, "outwardIssue": {"key": "RHAI-9002"},
        }]
        with patch("submit.verify_jira_auth"), \
             patch("jira_utils.get_issue", return_value={"fields": {}}) as lookup:
            assert submit_module._submit_strategy(
                "s", "u", "t", "RHAISTRAT-9999", plan, False) == (0, 1)
        lookup.assert_called_once_with("s", "u", "t", "RHAI-9002", fields=["issuetype"])
        self._assert_no_writes(submission_boundaries)
        assert "RHAI-9002" in capsys.readouterr().err

    def test_partial_resume_keeps_recorded_epic_and_creates_only_missing_one(
            self, tmp_dir, submission_boundaries):
        _setup_strategy(epic_count=2)
        _run_fm("set", "artifacts/epic-tasks/RHAISTRAT-9999-E001.md",
                "jira_key=RHAI-9002")
        plan = _build_plan(_scan_epics("artifacts", "RHAISTRAT-9999"), set())
        with patch("jira_utils.api_call_with_retry", return_value={
            "accountId": "test-account", "active": True,
        }) as reads, patch("submit.has_existing_epics") as child_check:
            assert submit_module._submit_strategy(
                "s", "u", "t", "RHAISTRAT-9999", plan, False) == (1, 0)

        reads.assert_called_once_with("s", "/myself", "u", "t")
        child_check.assert_not_called()
        submission_boundaries[0]["create_issue"].assert_called_once()
        first, _ = read_frontmatter(plan[0]["path"])
        second, _ = read_frontmatter(plan[1]["path"])
        assert first["jira_key"] == "RHAI-9002"
        assert second["jira_key"] == "RHAI-9003"

    def test_dry_run_performs_no_jira_reads_or_writes(
            self, tmp_dir, submission_boundaries):
        plan = self._plan()
        with patch("submit.verify_jira_auth") as auth, \
             patch("submit.has_existing_epics") as child_check, \
             patch("jira_utils.api_call_with_retry") as reads:
            assert submit_module._submit_strategy(
                None, None, None, "RHAISTRAT-9999", plan, True) == (1, 0)
        auth.assert_not_called()
        child_check.assert_not_called()
        reads.assert_not_called()
        submission_boundaries[1].assert_not_called()
        self._assert_no_writes(submission_boundaries)

    @pytest.mark.parametrize("result,reason,recovery", [
        ({"issues": [], "isLast": True}, "empty or incomplete",
         "retry the same submission with its checks enabled"),
        ({"issues": [{"key": "RHAISTRAT-9999"}, {"key": "RHOAIENG-9002"}],
          "isLast": True}, "already has linked epics",
         "Review the existing epics instead of submitting a fresh breakdown"),
    ])
    def test_cli_explains_why_submission_is_blocked_and_how_to_recover(
            self, tmp_dir, submission_boundaries, capsys, result, reason, recovery):
        self._plan()
        with patch.object(sys, "argv", [SCRIPT, "RHAISTRAT-9999"]), \
             patch("submit.require_env", return_value=("s", "u", "t")), \
             patch("jira_utils.api_call_with_retry", side_effect=[
                 {"accountId": "test-account", "active": True},
                 result,
             ]):
            with pytest.raises(SystemExit) as failure:
                submit_module.main()
        assert failure.value.code == 1
        self._assert_no_writes(submission_boundaries)
        output = capsys.readouterr().err
        assert "Submission blocked for RHAISTRAT-9999" in output
        assert reason in output
        assert "No Jira writes were made for RHAISTRAT-9999 in this attempt." in output
        assert recovery in output
        assert "original artifacts" in output
        assert "recorded jira_key values" in output

    def test_failure_message_does_not_deny_writes_for_an_earlier_strategy(
            self, tmp_dir, submission_boundaries, capsys):
        _setup_strategy("RHAISTRAT-9998", epic_count=1)
        self._plan()
        submission_boundaries[1].side_effect = lambda server, user, token, key, **kwargs: {
            "key": key, "fields": {"issuelinks": [], "assignee": None},
        }
        auth_error = urllib.error.HTTPError("https://jira.example.com", 401,
                                            "Unauthorized", {}, None)
        with patch.object(sys, "argv", [SCRIPT, "RHAISTRAT-9998", "RHAISTRAT-9999"]), \
             patch("submit.require_env", return_value=("s", "u", "t")), \
             patch("jira_utils.api_call_with_retry", side_effect=[
                 {"accountId": "test-account", "active": True},
                 {"issues": [{"key": "RHAISTRAT-9998"}], "isLast": True},
                 auth_error,
             ]):
            with pytest.raises(SystemExit) as failure:
                submit_module.main()

        assert failure.value.code == 1
        submission_boundaries[0]["create_issue"].assert_called_once()
        output = capsys.readouterr()
        assert "1 epics created" in output.out
        assert "No Jira writes were made for RHAISTRAT-9999 in this attempt." in output.err
        assert "No Jira writes were made for RHAISTRAT-9998" not in output.err
        assert "HTTP 401" in output.err


class TestDryRun:
    """End-to-end dry-run tests via the CLI."""

    def test_dry_run_creates_plan(self, tmp_dir):
        _setup_strategy()
        result = _run_submit("--dry-run", "RHAISTRAT-9999")
        assert "RHAISTRAT-9999-E001" in result.stdout
        assert "RHAISTRAT-9999-E002" in result.stdout
        assert "DRY RUN" in result.stdout
        assert "2 epics created" in result.stdout

    def test_dry_run_skips_failing_review(self, tmp_dir):
        _setup_strategy(review_pass=False, review_score=5)
        result = _run_submit("--dry-run", "RHAISTRAT-9999")
        assert "SKIP" in result.stdout
        assert "1 strategies skipped" in result.stdout

    def test_dry_run_shows_priority_mapping(self, tmp_dir):
        _setup_strategy()
        result = _run_submit("--dry-run", "RHAISTRAT-9999")
        assert "Critical" in result.stdout  # P0

    def test_dry_run_shows_dependencies(self, tmp_dir):
        _setup_strategy()
        result = _run_submit("--dry-run", "RHAISTRAT-9999")
        assert "blocked by" in result.stdout

    def test_dry_run_shows_component_warning(self, tmp_dir):
        _setup_strategy()
        result = _run_submit("--dry-run", "RHAISTRAT-9999")
        # Component shows in parens because no mapping exists
        assert "(test-component)" in result.stdout
        assert "not in RHAI component list" in result.stderr

    def test_dry_run_all(self, tmp_dir):
        _setup_strategy("RHAISTRAT-1001")
        _setup_strategy("RHAISTRAT-1002")
        result = _run_submit("--dry-run", "--all")
        assert "RHAISTRAT-1001" in result.stdout
        assert "RHAISTRAT-1002" in result.stdout

    def test_all_with_no_decompositions_succeeds(self, tmp_dir):
        os.makedirs("artifacts/epic-tasks")
        result = _run_submit("--dry-run", "--all")
        assert result.returncode == 0
        assert "No decompositions found" in result.stdout

    def test_all_with_missing_artifacts_dir_fails(self, tmp_dir):
        result = subprocess.run(
            [PYTHON, SCRIPT, "--dry-run", "--all",
             "--artifacts-dir", "does-not-exist"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "is not a directory" in result.stderr

    def test_dry_run_labels(self, tmp_dir):
        _setup_strategy()
        result = _run_submit("--dry-run", "RHAISTRAT-9999")
        assert "epic-creator-auto-decomposed" in result.stdout

    def test_dry_run_shows_already_created(self, tmp_dir):
        _setup_strategy()
        _run_fm("set", "artifacts/epic-tasks/RHAISTRAT-9999-E001.md",
                "jira_key=RHAI-100")
        result = _run_submit("--dry-run", "RHAISTRAT-9999")
        assert "Already created" in result.stdout
        assert "RHAI-100" in result.stdout
        # Only E002 is pending
        assert "1 epics created" in result.stdout
