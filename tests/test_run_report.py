"""Run reports keep current outcomes separate from review recovery history."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/generate_run_report.py"
START = "2026-01-01T00:00:00Z"


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _review(path, passed=True, **updates):
    data = {
        "strat_id": "RHAISTRAT-9001",
        "score": 14 if passed else 8,
        "pass": passed,
        "recommendation": "accept" if passed else "revise",
        "issues": [],
    }
    data.update(updates)
    _write(path, "---\n" + yaml.safe_dump(data) + "---\nReview body\n")


def _setup(root, strat_id="RHAISTRAT-9001", passed=True):
    _write(root / f"artifacts/epic-tasks/{strat_id}-decomposition.md",
           "---\nepic_count: 2\n---\nSummary\n")
    review = root / f"artifacts/epic-reviews/{strat_id}-decomp-review.md"
    _review(review, passed=passed, strat_id=strat_id)
    return review


def _history(root, strat_id, number, passed, changed):
    path = (root / "artifacts/epic-reviews/history" / strat_id
            / f"attempt-{number:04d}.md")
    _review(path, passed=passed, strat_id=strat_id)
    _write(path.with_suffix(".json"), json.dumps({
        "revision_changed": changed,
        "status": "passed" if passed else "failed",
    }))


def _report(root, *ids):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--start-time", START,
         "--batch-size", "10", *ids],
        cwd=root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return yaml.safe_load(
        (root / "artifacts/decompose-runs/2026-01-01T00-00-00Z.yaml")
        .read_text())


def test_reports_current_status_and_preserves_existing_fields(tmp_path):
    _setup(tmp_path)
    report = _report(tmp_path, "RHAISTRAT-9001")
    assert report["started"] == START
    assert report["completed"]
    assert report["batch_size"] == 10
    assert report["total"] == 1
    assert report["results"] == [{
        "strat_id": "RHAISTRAT-9001", "status": "passed", "epic_count": 2,
        "score": 14, "review_recoveries_without_edits": 0,
    }]
    assert report["recovered_without_edits"] == []


def test_reports_recovery_without_revision_edits(tmp_path):
    _setup(tmp_path)
    _history(tmp_path, "RHAISTRAT-9001", 1, passed=False, changed=False)
    report = _report(tmp_path, "RHAISTRAT-9001")
    assert report["results"][0]["status"] == "passed"
    assert report["results"][0]["review_recoveries_without_edits"] == 1
    assert report["recovered_without_edits"] == ["RHAISTRAT-9001"]


def test_does_not_label_recovery_after_edits_as_unchanged(tmp_path):
    _setup(tmp_path)
    _history(tmp_path, "RHAISTRAT-9001", 1, passed=False, changed=True)
    report = _report(tmp_path, "RHAISTRAT-9001")
    assert report["results"][0]["review_recoveries_without_edits"] == 0
    assert report["recovered_without_edits"] == []


def test_only_counts_adjacent_failed_to_passing_review_attempts(tmp_path):
    _setup(tmp_path)
    _history(tmp_path, "RHAISTRAT-9001", 1, passed=False, changed=False)
    _history(tmp_path, "RHAISTRAT-9001", 2, passed=False, changed=True)
    report = _report(tmp_path, "RHAISTRAT-9001")
    assert report["results"][0]["review_recoveries_without_edits"] == 0


def test_history_cannot_replace_current_failed_outcome(tmp_path):
    _setup(tmp_path, passed=False)
    _history(tmp_path, "RHAISTRAT-9001", 1, passed=True, changed=False)
    report = _report(tmp_path, "RHAISTRAT-9001")
    assert report["results"][0]["status"] == "failed"
    assert report["results"][0]["score"] == 8
    assert report["recovered_without_edits"] == []


@pytest.mark.parametrize("content", [
    None,
    "---\nscore: [unclosed\n---\n",
    "---\nstrat_id: RHAISTRAT-9001\nscore: 14\nrecommendation: accept\n---\n",
])
def test_unusable_current_review_is_an_error_not_history_pass(tmp_path, content):
    current = _setup(tmp_path)
    if content is None:
        current.unlink()
    else:
        current.write_text(content)
    _history(tmp_path, "RHAISTRAT-9001", 1, passed=True, changed=False)
    report = _report(tmp_path, "RHAISTRAT-9001")
    entry = report["results"][0]
    assert entry["status"] == "error"
    assert entry["error"]
    assert report["recovered_without_edits"] == []


def test_preserves_reported_agent_error(tmp_path):
    review = _setup(tmp_path)
    _review(review, passed=False, error="Reviewer failed")
    report = _report(tmp_path, "RHAISTRAT-9001")
    assert report["results"][0]["status"] == "error"
    assert report["results"][0]["error"] == "Reviewer failed"


def test_mixed_batch_uses_each_strategy_history(tmp_path):
    _setup(tmp_path)
    _setup(tmp_path, "RHAISTRAT-9002", passed=False)
    _history(tmp_path, "RHAISTRAT-9001", 1, passed=False, changed=False)
    report = _report(tmp_path, "RHAISTRAT-9001", "RHAISTRAT-9002")
    assert [entry["status"] for entry in report["results"]] == ["passed", "failed"]
    assert report["recovered_without_edits"] == ["RHAISTRAT-9001"]


def test_can_read_strategy_ids_from_existing_pipeline_file(tmp_path):
    _setup(tmp_path)
    _write(tmp_path / "tmp/pipeline-all-ids.txt", "RHAISTRAT-9001\n")
    report = _report(tmp_path)
    assert report["results"][0]["strat_id"] == "RHAISTRAT-9001"
