"""Review validation and history at the submission/recovery boundary."""
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from review_utils import archive_review, load_review, unchanged_review_recoveries


def review(path, **changes):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"strat_id": "RHAISTRAT-9001", "score": 14,
            "pass": True, "recommendation": "accept", **changes}
    path.write_text("---\n" + yaml.safe_dump(data) + "---\nReview\n")


@pytest.mark.parametrize("changes,expected", [
    ({}, "passed"), ({"pass": False}, "failed"), ({"score": 9}, "failed"),
    ({"recommendation": "revise"}, "failed"), ({"score": True}, "error"),
    ({"score": 15}, "error"), ({"score": "14"}, "error"),
    ({"error": "reviewer failed"}, "error"),
])
def test_quality_and_technical_failure_are_distinct(tmp_path, changes, expected):
    path = tmp_path / "review.md"
    review(path, **changes)
    assert load_review(path)[0] == expected


def test_history_preserves_each_attempt_and_never_approves_a_missing_current_review(tmp_path):
    path = tmp_path / "epic-reviews/RHAISTRAT-9001-decomp-review.md"
    review(path, score=8, **{"pass": False, "recommendation": "revise"})
    original = path.read_bytes()
    first = archive_review("RHAISTRAT-9001", False, tmp_path)
    assert first.read_bytes() == original
    assert not path.exists()
    assert load_review(path)[0] == "error"
    review(path)
    assert unchanged_review_recoveries("RHAISTRAT-9001", tmp_path) == 1
    second = archive_review("RHAISTRAT-9001", None, tmp_path)
    assert second != first and first.read_bytes() == original
    assert unchanged_review_recoveries("RHAISTRAT-9001", tmp_path) == 1


def test_missing_history_metadata_is_not_a_recovery_claim(tmp_path):
    path = tmp_path / "epic-reviews/RHAISTRAT-9001-decomp-review.md"
    review(path, score=8, **{"pass": False, "recommendation": "revise"})
    archived = archive_review("RHAISTRAT-9001", False, tmp_path)
    archived.with_suffix(".json").unlink()
    review(path)
    assert unchanged_review_recoveries("RHAISTRAT-9001", tmp_path) == 0


def test_wrong_strategy_cannot_supply_a_passing_review(tmp_path):
    path = tmp_path / "RHAISTRAT-9002-decomp-review.md"
    review(path)  # Metadata belongs to RHAISTRAT-9001.
    assert load_review(path)[0] == "error"


def test_invalid_history_metadata_is_not_a_recovery_claim(tmp_path):
    path = tmp_path / "epic-reviews/RHAISTRAT-9001-decomp-review.md"
    review(path, score=8, **{"pass": False, "recommendation": "revise"})
    archived = archive_review("RHAISTRAT-9001", False, tmp_path)
    archived.with_suffix(".json").write_text("[]\n")
    review(path)
    assert unchanged_review_recoveries("RHAISTRAT-9001", tmp_path) == 0
