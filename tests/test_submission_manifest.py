import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import submission_manifest


@pytest.fixture
def context(tmp_path):
    strategy_id = "RHAISTRAT-1001"
    review_path = tmp_path / "review.md"
    review_path.write_text("---\npass: true\nscore: 14\n---\n")
    plan = [
        {
            "epic_id": f"{strategy_id}-E001",
            "title": "First epic",
            "priority": "Major",
            "component": "Platform",
            "labels": ["epic-creator-auto-created"],
            "dependencies": [],
            "path": str(tmp_path / "E001.md"),
        },
        {
            "epic_id": f"{strategy_id}-E002",
            "title": "Second epic",
            "priority": "Minor",
            "component": None,
            "labels": ["epic-creator-auto-created"],
            "dependencies": [f"{strategy_id}-E001"],
            "path": str(tmp_path / "E002.md"),
        },
    ]
    for entry in plan:
        Path(entry["path"]).write_text(
            f"---\nepic_id: {entry['epic_id']}\n---\n")
    return {
        "strategy_id": strategy_id,
        "review_path": str(review_path),
        "plan": plan,
    }


def _build(context):
    return submission_manifest.build_manifest(
        context["strategy_id"], context["plan"],
        {"pass": True, "score": 14}, context["review_path"])


def test_builds_valid_manifest_with_stable_run_id(context):
    first = _build(context)
    second = _build(context)
    submission_manifest.validate_manifest(first)
    assert first["run_id"] == second["run_id"]
    assert first["run_id"].startswith(context["strategy_id"] + "-")


def test_jira_key_does_not_change_approved_content_digest(context):
    first = _build(context)
    epic_path = context["plan"][0]["path"]
    content = Path(epic_path).read_text()
    Path(epic_path).write_text(
        content.replace("---\n", "---\njira_key: RHAI-100\n", 1))
    second = _build(context)
    assert first["epics"][0]["source_sha256"] == \
        second["epics"][0]["source_sha256"]
    assert first["run_id"] == second["run_id"]


def test_branch_plan_content_is_part_of_manifest(context, tmp_path):
    branch_path = tmp_path / "RHAISTRAT-1001-BRANCH-A-E003.md"
    branch_path.write_text(
        "---\n"
        "epic_id: RHAISTRAT-1001-BRANCH-A-E003\n"
        "branch: A\n"
        "gated_by: RHAISTRAT-1001-E001\n"
        "---\n"
        "## Description\n\nApproved branch content.\n")
    branches = {("A", "RHAISTRAT-1001-E001"): [(
        str(branch_path),
        {
            "epic_id": "RHAISTRAT-1001-BRANCH-A-E003",
            "branch": "A",
            "gated_by": "RHAISTRAT-1001-E001",
        },
        "body",
    )]}
    first = submission_manifest.build_manifest(
        context["strategy_id"], context["plan"],
        {"pass": True, "score": 14}, context["review_path"], branches=branches)
    assert len(first["branch_plans"]) == 1
    branch_path.write_text(
        branch_path.read_text() + "Changed content.\n")
    second = submission_manifest.build_manifest(
        context["strategy_id"], context["plan"],
        {"pass": True, "score": 14}, context["review_path"], branches=branches)
    assert first["branch_plans"][0]["source_sha256"] != \
        second["branch_plans"][0]["source_sha256"]
    assert first["run_id"] != second["run_id"]


def test_rejects_unapproved_review(context):
    with pytest.raises(submission_manifest.SubmissionManifestError):
        submission_manifest.build_manifest(
            context["strategy_id"], context["plan"],
            {"pass": False, "score": 5}, context["review_path"])


def test_rejects_unknown_dependency(context):
    context["plan"][1]["dependencies"] = ["RHAISTRAT-1001-E999"]
    with pytest.raises(submission_manifest.SubmissionManifestError):
        _build(context)


def test_rejects_duplicate_epic_id(context):
    context["plan"][1]["epic_id"] = context["plan"][0]["epic_id"]
    with pytest.raises(submission_manifest.SubmissionManifestError):
        _build(context)


def test_rejects_dependency_cycle(context):
    context["plan"][0]["dependencies"] = ["RHAISTRAT-1001-E002"]
    context["plan"][1]["dependencies"] = ["RHAISTRAT-1001-E001"]
    with pytest.raises(submission_manifest.SubmissionManifestError):
        _build(context)


def test_manifest_round_trip(context, tmp_path):
    manifest = _build(context)
    path = tmp_path / "manifest.yaml"
    submission_manifest.write_manifest(path, manifest)
    assert submission_manifest.read_manifest(path) == manifest


def test_invalid_manifest_is_not_written(context, tmp_path):
    manifest = _build(context)
    manifest["approved"] = False
    path = tmp_path / "invalid.yaml"
    with pytest.raises(submission_manifest.SubmissionManifestError):
        submission_manifest.write_manifest(path, manifest)
    assert not path.exists()


def test_malformed_manifest_is_reported_as_manifest_error(tmp_path):
    path = tmp_path / "malformed.yaml"
    path.write_text("approved: [unclosed\n")
    with pytest.raises(submission_manifest.SubmissionManifestError):
        submission_manifest.read_manifest(path)


def test_manifest_path_is_exported(context):
    assert submission_manifest.manifest_path(
        "artifacts", context["strategy_id"]) == os.path.join(
        "artifacts", "submission-manifests",
        f"{context['strategy_id']}.yaml")
