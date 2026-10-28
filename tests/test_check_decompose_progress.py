#!/usr/bin/env python3
"""Tests for scripts/check_decompose_progress.py — phase checking and revised flag."""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_decompose_progress import (
    _check_phase,
    _format_status,
    check_id,
)
from pipeline_status import write_status


# ── check_id ──


class TestCheckId:
    def test_missing_file_is_pending(self, tmp_path):
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("fetch", "RHAISTRAT-1") == "pending"

    @pytest.mark.parametrize(("status", "expected"), [
        ("skipped", "completed"),
        ("invalid", "error"),
    ])
    def test_fetch_terminal_status_does_not_remain_pending(
            self, tmp_path, monkeypatch, status, expected):
        monkeypatch.chdir(tmp_path)
        write_status("RHOAIENG-1", status, "fetch", "synthetic outcome")
        assert check_id("fetch", "RHOAIENG-1") == expected

    def test_existing_file_is_completed(self, tmp_path):
        f = tmp_path / "RHAISTRAT-1.md"
        f.write_text("content")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("fetch", "RHAISTRAT-1") == "completed"

    def test_review_phase_score_present(self, tmp_path):
        """Review phase: file with score -> completed."""
        f = tmp_path / "RHAISTRAT-1-decomp-review.md"
        f.write_text("---\nscore: 7\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"review_decomp": lambda id: str(tmp_path / f"{id}-decomp-review.md")},
        ):
            assert check_id("review_decomp", "RHAISTRAT-1") == "completed"

    def test_review_phase_score_missing(self, tmp_path):
        """Review phase: file without score -> pending."""
        f = tmp_path / "RHAISTRAT-1-decomp-review.md"
        f.write_text("---\ntitle: test\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"review_decomp": lambda id: str(tmp_path / f"{id}-decomp-review.md")},
        ):
            assert check_id("review_decomp", "RHAISTRAT-1") == "pending"

    def test_review_phase_error_flag(self, tmp_path):
        """Review phase: file with score + error -> error."""
        f = tmp_path / "RHAISTRAT-1-decomp-review.md"
        f.write_text("---\nscore: 5\nerror: true\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"review_decomp": lambda id: str(tmp_path / f"{id}-decomp-review.md")},
        ):
            assert check_id("review_decomp", "RHAISTRAT-1") == "error"

    def test_review_phase_unparseable_is_pending(self, tmp_path):
        """Review phase: unparseable frontmatter -> pending, not error.

        The review agent writes the body first and sets frontmatter via
        frontmatter.py seconds later; an unparseable file is mid-write.
        Treating it as error released the wait-for-wave barrier early
        (COMPLETED=0, ERRORS=1, pending=0 -> NEXT_POLL=0) in three cases of
        the 2026-08-19 eval run while the reviewer was still writing.
        """
        f = tmp_path / "RHAISTRAT-1-decomp-review.md"
        f.write_text("---\n: bad yaml [[\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"review_decomp": lambda id: str(tmp_path / f"{id}-decomp-review.md")},
        ):
            assert check_id("review_decomp", "RHAISTRAT-1") == "pending"

    def test_review_phase_body_without_frontmatter_is_pending(self, tmp_path):
        """Review phase: body-only file (the actual mid-write state) -> pending."""
        f = tmp_path / "RHAISTRAT-1-decomp-review.md"
        f.write_text("## Review Summary\n\nLooks good so far.\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"review_decomp": lambda id: str(tmp_path / f"{id}-decomp-review.md")},
        ):
            assert check_id("review_decomp", "RHAISTRAT-1") == "pending"

    def test_review_phase_empty_frontmatter_is_pending(self, tmp_path):
        """Review phase: empty frontmatter -> pending (mid-write)."""
        f = tmp_path / "RHAISTRAT-1-decomp-review.md"
        f.write_text("---\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"review_decomp": lambda id: str(tmp_path / f"{id}-decomp-review.md")},
        ):
            assert check_id("review_decomp", "RHAISTRAT-1") == "pending"

    def test_revise_phase_unparseable_is_pending(self, tmp_path):
        """Revise phase mirrors the same body-then-frontmatter pattern."""
        f = tmp_path / "RHAISTRAT-1-decomposition.md"
        f.write_text("---\n: bad yaml [[\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"revise_decomp": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("revise_decomp", "RHAISTRAT-1") == "pending"


class TestDecomposePhase:
    """Tests for the decompose phase — waits for all epic files, not just summary."""

    def test_no_sentinel_is_pending(self, tmp_path):
        """Summary exists but no sentinel -> pending."""
        f = tmp_path / "RHAISTRAT-1-decomposition.md"
        f.write_text("---\nepic_count: 5\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"decompose": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("decompose", "RHAISTRAT-1") == "pending"

    def test_sentinel_true_is_completed(self, tmp_path):
        """decompose_complete: true -> completed."""
        f = tmp_path / "RHAISTRAT-1-decomposition.md"
        f.write_text("---\nepic_count: 5\ndecompose_complete: true\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"decompose": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("decompose", "RHAISTRAT-1") == "completed"

    def test_sentinel_false_is_pending(self, tmp_path):
        """decompose_complete: false -> pending (agent still writing)."""
        f = tmp_path / "RHAISTRAT-1-decomposition.md"
        f.write_text("---\nepic_count: 5\ndecompose_complete: false\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"decompose": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("decompose", "RHAISTRAT-1") == "pending"

    def test_missing_summary_is_pending(self, tmp_path):
        """No summary file at all -> pending."""
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"decompose": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("decompose", "RHAISTRAT-1") == "pending"

    def test_no_epic_count_is_pending(self, tmp_path):
        """Summary without epic_count -> pending."""
        f = tmp_path / "RHAISTRAT-1-decomposition.md"
        f.write_text("---\nparent_strat: RHAISTRAT-1\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"decompose": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("decompose", "RHAISTRAT-1") == "pending"



class TestReviseDecompPhase:
    """Tests for the revise_decomp phase — the revised flag fix."""

    def test_revised_true_is_completed(self, tmp_path):
        """revised: true -> completed (changes were made)."""
        f = tmp_path / "RHAISTRAT-1-decomposition.md"
        f.write_text("---\nrevised: true\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"revise_decomp": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("revise_decomp", "RHAISTRAT-1") == "completed"

    def test_revised_false_is_completed(self, tmp_path):
        """revised: false -> completed (no changes needed, but agent ran)."""
        f = tmp_path / "RHAISTRAT-1-decomposition.md"
        f.write_text("---\nrevised: false\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"revise_decomp": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("revise_decomp", "RHAISTRAT-1") == "completed"

    def test_revised_absent_is_pending(self, tmp_path):
        """No revised field -> pending (agent hasn't run yet)."""
        f = tmp_path / "RHAISTRAT-1-decomposition.md"
        f.write_text("---\nepic_count: 5\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"revise_decomp": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("revise_decomp", "RHAISTRAT-1") == "pending"

    def test_revised_missing_file_is_pending(self, tmp_path):
        """File doesn't exist -> pending."""
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"revise_decomp": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("revise_decomp", "RHAISTRAT-1") == "pending"

    def test_revised_bad_frontmatter_is_pending(self, tmp_path):
        """Unparseable frontmatter -> error."""
        f = tmp_path / "RHAISTRAT-1-decomposition.md"
        f.write_text("---\n: bad [[\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"revise_decomp": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("revise_decomp", "RHAISTRAT-1") == "pending"

    def test_revised_empty_frontmatter_is_pending(self, tmp_path):
        """Empty frontmatter -> error."""
        f = tmp_path / "RHAISTRAT-1-decomposition.md"
        f.write_text("---\n---\nBody\n")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"revise_decomp": lambda id: str(tmp_path / f"{id}-decomposition.md")},
        ):
            assert check_id("revise_decomp", "RHAISTRAT-1") == "pending"


# ── _check_phase ──


class TestCheckPhase:
    def test_all_pending(self, tmp_path):
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            completed, errors, pending, total, next_poll = \
                _check_phase("fetch", ["A", "B", "C"], fast=False)
            assert completed == 0
            assert pending == 3
            assert total == 3
            assert next_poll == 60

    def test_all_completed(self, tmp_path):
        for name in ["A", "B", "C"]:
            (tmp_path / f"{name}.md").write_text("done")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            completed, errors, pending, total, next_poll = \
                _check_phase("fetch", ["A", "B", "C"], fast=False)
            assert completed == 3
            assert pending == 0
            assert next_poll == 0

    def test_adaptive_interval_half(self, tmp_path):
        """50% complete -> 30s interval."""
        for name in ["A", "B"]:
            (tmp_path / f"{name}.md").write_text("done")
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            _, _, _, _, next_poll = \
                _check_phase("fetch", ["A", "B", "C", "D"], fast=False)
            assert next_poll == 30

    def test_fast_poll_caps_at_15(self, tmp_path):
        """Fast mode caps at 15s regardless of completion ratio."""
        with patch.dict(
            "check_decompose_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            _, _, _, _, next_poll = \
                _check_phase("fetch", ["A", "B", "C"], fast=True)
            assert next_poll == 15


# ── _format_status ──


class TestFormatStatus:
    def test_pending_format(self):
        s = _format_status("decompose", 2, 0, 3, 5, 30)
        assert s == "decompose: COMPLETED=2/5, PENDING=3, NEXT_POLL=30"

    def test_complete_format(self):
        s = _format_status("fetch", 5, 0, 0, 5, 0)
        assert s == "fetch: COMPLETED=5/5, NEXT_POLL=0"

    def test_error_format(self):
        s = _format_status("review_decomp", 3, 1, 1, 5, 15)
        assert s == "review_decomp: COMPLETED=3/5, PENDING=1, ERRORS=1, NEXT_POLL=15"
