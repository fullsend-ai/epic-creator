#!/usr/bin/env python3
"""Tests for scripts/pipeline_state.py — first-revise skip on a clean review pass."""
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from artifact_utils import read_frontmatter
from pipeline_state import PHASE_CONFIG, advance


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for d in ("tmp", "artifacts/epic-reviews", "artifacts/epic-tasks"):
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


def _write_md(path, data):
    path.write_text("---\n" + yaml.dump(data) + "---\nbody\n")


def _setup(workdir, reviews):
    """reviews: {strat_id: review frontmatter dict, or None for no review file}."""
    (workdir / "tmp/pipeline-active-ids.txt").write_text(
        "".join(f"{i}\n" for i in reviews))
    for strat_id, review in reviews.items():
        _write_md(workdir / f"artifacts/epic-tasks/{strat_id}-decomposition.md",
                  {"parent_strat": strat_id, "epic_count": 2,
                   "critical_path_length": 2, "revised": False})
        if review is not None:
            _write_md(workdir / f"artifacts/epic-reviews/{strat_id}-decomp-review.md",
                      {"strat_id": strat_id, **review})


def _revise_ids(workdir):
    return (workdir / "tmp/pipeline-revise-ids.txt").read_text().split()


def _revised_flag(workdir, strat_id):
    data, _ = read_frontmatter(
        str(workdir / f"artifacts/epic-tasks/{strat_id}-decomposition.md"))
    return data.get("revised", "absent")


CLEAN = {"score": 14, "pass": True, "recommendation": "accept", "issues": []}
MINOR = {"score": 13, "pass": True, "recommendation": "accept",
         "issues": [{"severity": "minor", "criterion": "DAG Coherence",
                     "description": "edge not justified"}]}
FAIL = {"score": 8, "pass": False, "recommendation": "revise",
        "issues": [{"severity": "major", "criterion": "HLR Coverage",
                    "description": "HLR-3 unmapped"}]}


class TestReviewDecompTransition:
    def test_all_clean_pass_skips_revise(self, workdir):
        _setup(workdir, {"RHAISTRAT-1": CLEAN, "RHAISTRAT-2": CLEAN})
        nxt, summary = advance({"phase": "REVIEW_DECOMP"})
        assert nxt == "BATCH_DONE"
        assert "revise skipped" in summary
        # Sentinel left at the schema default; nothing was cleared.
        assert _revised_flag(workdir, "RHAISTRAT-1") is False
        assert not (workdir / "tmp/pipeline-revise-ids.txt").exists()

    def test_failing_review_revises(self, workdir):
        _setup(workdir, {"RHAISTRAT-1": FAIL})
        nxt, _ = advance({"phase": "REVIEW_DECOMP"})
        assert nxt == "REVISE_DECOMP"
        assert _revise_ids(workdir) == ["RHAISTRAT-1"]
        assert _revised_flag(workdir, "RHAISTRAT-1") == "absent"

    def test_pass_with_minor_issues_still_revises(self, workdir):
        _setup(workdir, {"RHAISTRAT-1": MINOR})
        nxt, _ = advance({"phase": "REVIEW_DECOMP"})
        assert nxt == "REVISE_DECOMP"
        assert _revise_ids(workdir) == ["RHAISTRAT-1"]

    def test_missing_review_revises(self, workdir):
        _setup(workdir, {"RHAISTRAT-1": None})
        nxt, _ = advance({"phase": "REVIEW_DECOMP"})
        assert nxt == "REVISE_DECOMP"
        assert _revise_ids(workdir) == ["RHAISTRAT-1"]

    def test_review_with_error_revises(self, workdir):
        _setup(workdir, {"RHAISTRAT-1": {**CLEAN, "error": "agent crashed"}})
        nxt, _ = advance({"phase": "REVIEW_DECOMP"})
        assert nxt == "REVISE_DECOMP"

    def test_review_missing_pass_field_revises(self, workdir):
        review = {k: v for k, v in CLEAN.items() if k != "pass"}
        _setup(workdir, {"RHAISTRAT-1": review})
        nxt, _ = advance({"phase": "REVIEW_DECOMP"})
        assert nxt == "REVISE_DECOMP"

    @pytest.mark.parametrize("override", [
        {"score": 0},
        {"score": 9},
        {"score": None},
        {"recommendation": "revise"},
    ])
    def test_inconsistent_pass_revises(self, workdir, override):
        """pass: true that contradicts its own score or recommendation is not clean."""
        _setup(workdir, {"RHAISTRAT-1": {**CLEAN, **override}})
        nxt, _ = advance({"phase": "REVIEW_DECOMP"})
        assert nxt == "REVISE_DECOMP"

    def test_unreadable_review_revises(self, workdir):
        _setup(workdir, {"RHAISTRAT-1": None})
        (workdir / "artifacts/epic-reviews/RHAISTRAT-1-decomp-review.md"
         ).write_text("---\n: [unclosed\n---\n")
        nxt, _ = advance({"phase": "REVIEW_DECOMP"})
        assert nxt == "REVISE_DECOMP"

    def test_mixed_batch_revises_only_non_clean(self, workdir):
        _setup(workdir, {"RHAISTRAT-1": CLEAN, "RHAISTRAT-2": FAIL,
                         "RHAISTRAT-3": MINOR})
        nxt, summary = advance({"phase": "REVIEW_DECOMP"})
        assert nxt == "REVISE_DECOMP"
        assert "2 to revise" in summary
        assert _revise_ids(workdir) == ["RHAISTRAT-2", "RHAISTRAT-3"]
        # The skipped ID keeps its sentinel so the poller reports it done...
        assert _revised_flag(workdir, "RHAISTRAT-1") is False
        # ...and only revised IDs are cleared to pending.
        assert _revised_flag(workdir, "RHAISTRAT-2") == "absent"
        assert _revised_flag(workdir, "RHAISTRAT-3") == "absent"

    def test_dry_run_writes_nothing(self, workdir):
        _setup(workdir, {"RHAISTRAT-1": FAIL})
        nxt, _ = advance({"phase": "REVIEW_DECOMP"}, dry_run=True)
        assert nxt == "REVISE_DECOMP"
        assert not (workdir / "tmp/pipeline-revise-ids.txt").exists()
        assert _revised_flag(workdir, "RHAISTRAT-1") is False


class TestReviseWaveUsesReviseIds:
    def test_revise_decomp_dispatches_from_revise_ids(self):
        assert PHASE_CONFIG["REVISE_DECOMP"]["ids_file"] == "tmp/pipeline-revise-ids.txt"

    def test_re_review_check_first_cycle_reads_revise_ids(self, workdir):
        """Cycle 0 must check only the revised subset, not every active ID."""
        _setup(workdir, {"RHAISTRAT-1": CLEAN, "RHAISTRAT-2": FAIL})
        advance({"phase": "REVIEW_DECOMP"})
        # Simulate the revise agent for RHAISTRAT-2 making changes. RHAISTRAT-1
        # was skipped; force its flag true to prove it is not re-reviewed.
        for strat_id in ("RHAISTRAT-1", "RHAISTRAT-2"):
            _write_md(workdir / f"artifacts/epic-tasks/{strat_id}-decomposition.md",
                      {"parent_strat": strat_id, "epic_count": 2,
                       "critical_path_length": 2, "revised": True})
        nxt, _ = advance({"phase": "RE_REVIEW_CHECK", "revise_cycle": 0})
        assert nxt == "RE_REVIEW"
        assert _revise_ids(workdir) == ["RHAISTRAT-2"]
        assert (workdir / "artifacts/epic-reviews/RHAISTRAT-1-decomp-review.md").exists()
        assert not (workdir / "artifacts/epic-reviews/RHAISTRAT-2-decomp-review.md").exists()
