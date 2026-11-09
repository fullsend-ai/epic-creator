"""Recovery regressions using real pipeline transitions and artifacts."""
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from artifact_utils import read_frontmatter
from pipeline_state import advance
from submit import _check_review_passed
from check_decompose_progress import check_id
from pipeline_state import PHASE_CONFIG
from review_utils import load_review


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for directory in ("tmp", "artifacts/epic-tasks", "artifacts/epic-reviews"):
        (tmp_path / directory).mkdir(parents=True)
    return tmp_path


def write_md(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + yaml.safe_dump(data) + "---\nBody\n")


def setup_strategy(root, strat_id="RHAISTRAT-9001", passed=False, revised=False):
    write_md(root / f"artifacts/epic-tasks/{strat_id}-decomposition.md",
             {"parent_strat": strat_id, "epic_count": 1,
              "critical_path_length": 1, "revised": revised,
              "decompose_complete": True})
    write_md(root / f"artifacts/epic-reviews/{strat_id}-decomp-review.md",
             {"strat_id": strat_id, "score": 14 if passed else 8,
              "pass": passed, "recommendation": "accept" if passed else "revise"})
    for name in ("active", "revise", "all"):
        (root / f"tmp/pipeline-{name}-ids.txt").write_text(strat_id + "\n")


def test_failed_unchanged_review_is_freshly_reviewed(workdir):
    setup_strategy(workdir)
    next_phase, _ = advance({"phase": "RE_REVIEW_CHECK", "revise_cycle": 0})
    assert next_phase == "RE_REVIEW"
    assert not (workdir / "artifacts/epic-reviews/RHAISTRAT-9001-decomp-review.md").exists()
    assert _check_review_passed("artifacts", "RHAISTRAT-9001")[0] is False


def test_new_batch_resets_revision_state(workdir):
    (workdir / "tmp/pipeline-batch-2-ids.txt").write_text("RHAISTRAT-9002\n")
    (workdir / "tmp/pipeline-revise-ids.txt").write_text("RHAISTRAT-9001\n")
    state = {"phase": "BATCH_START", "batch": 1, "revise_cycle": 2}
    assert advance(state)[0] == "FETCH"
    assert state["revise_cycle"] == 0
    assert (workdir / "tmp/pipeline-revise-ids.txt").read_text() == ""


@pytest.mark.parametrize("content", [None, "---\nscore: [broken\n---\n",
                                    "---\nscore: 12\nrecommendation: accept\n---\n"])
def test_invalid_review_uses_error_retry_and_does_not_crash_submit(workdir, content):
    setup_strategy(workdir)
    path = workdir / "artifacts/epic-reviews/RHAISTRAT-9001-decomp-review.md"
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    assert _check_review_passed("artifacts", "RHAISTRAT-9001")[0] is False
    assert advance({"phase": "REVISE_CHECK", "revise_cycle": 0})[0] == "BATCH_DONE"
    assert advance({"phase": "BATCH_DONE", "batch": 1,
                    "total_batches": 1, "retry_cycle": 0})[0] == "ERROR_COLLECT"


@pytest.mark.parametrize("passed,revised,expected", [
    (True, False, "BATCH_DONE"), (True, True, "RE_REVIEW"),
    (False, True, "RE_REVIEW"),
])
def test_re_review_selection(workdir, passed, revised, expected):
    setup_strategy(workdir, passed=passed, revised=revised)
    assert advance({"phase": "RE_REVIEW_CHECK"})[0] == expected


def test_mixed_batch_keeps_passes_and_errors_out_of_quality_re_review(workdir):
    ids = [f"RHAISTRAT-{9000+i}" for i in range(1, 5)]
    for key, passed, revised in zip(ids, [False, True, True, False],
                                   [False, False, True, False]):
        setup_strategy(workdir, key, passed, revised)
    error_path = workdir / f"artifacts/epic-reviews/{ids[3]}-decomp-review.md"
    data, _ = read_frontmatter(str(error_path))
    write_md(error_path, {**data, "error": "review agent failed"})
    (workdir / "tmp/pipeline-revise-ids.txt").write_text("\n".join(ids))
    assert advance({"phase": "RE_REVIEW_CHECK"})[0] == "RE_REVIEW"
    assert (workdir / "tmp/pipeline-revise-ids.txt").read_text().split() == [ids[0], ids[2]]
    for key in (ids[1], ids[3]):
        assert (workdir / f"artifacts/epic-reviews/{key}-decomp-review.md").exists()


def test_first_revision_uses_shared_list_and_preserves_completion_contract(workdir):
    setup_strategy(workdir)
    assert advance({"phase": "REVIEW_DECOMP"})[0] == "REVISE_DECOMP"
    assert PHASE_CONFIG["REVISE_DECOMP"]["ids_file"] == "tmp/pipeline-revise-ids.txt"
    assert check_id("revise_decomp", "RHAISTRAT-9001") == "pending"
    path = workdir / "artifacts/epic-tasks/RHAISTRAT-9001-decomposition.md"
    data, _ = read_frontmatter(str(path))
    write_md(path, {**data, "revised": False})
    assert check_id("revise_decomp", "RHAISTRAT-9001") == "completed"
    assert advance({"phase": "RE_REVIEW_CHECK"})[0] == "RE_REVIEW"
    assert check_id("review_decomp", "RHAISTRAT-9001") == "pending"


def test_error_retry_gets_fresh_batch_and_completion_sentinels(workdir):
    import error_collect
    setup_strategy(workdir)
    path = workdir / "artifacts/epic-reviews/RHAISTRAT-9001-decomp-review.md"
    data, _ = read_frontmatter(str(path))
    write_md(path, {**data, "error": "review agent failed"})
    state = {"phase": "BATCH_DONE", "batch": 2, "total_batches": 2,
             "revise_cycle": 2, "retry_cycle": 0}
    assert advance({**state, "phase": "REVIEW_DECOMP"})[0] == "BATCH_DONE"
    assert advance(state)[0] == "ERROR_COLLECT"
    state["phase"] = "ERROR_COLLECT"
    (workdir / "tmp/pipeline-state.yaml").write_text(yaml.safe_dump(state))
    error_collect.main()
    state = yaml.safe_load((workdir / "tmp/pipeline-state.yaml").read_text())
    assert state["retry_cycle"] == 1
    state["phase"] = advance(state)[0]
    assert advance(state)[0] == "FETCH"
    assert state["revise_cycle"] == 0
    assert (workdir / "tmp/pipeline-revise-ids.txt").read_text() == ""
    assert check_id("decompose", "RHAISTRAT-9001") == "pending"
    assert check_id("review_decomp", "RHAISTRAT-9001") == "pending"
    assert list((workdir / "artifacts/epic-reviews/history/RHAISTRAT-9001").glob("*.md"))
    # A second technical failure cannot trigger a second error-retry batch.
    assert advance({"phase": "BATCH_DONE", "batch": 1, "total_batches": 1,
                    "retry_cycle": 1})[0] == "REPORT"


def test_second_batch_reviews_its_own_ids(workdir):
    setup_strategy(workdir, revised=False)
    setup_strategy(workdir, "RHAISTRAT-9002", revised=False)
    (workdir / "tmp/pipeline-revise-ids.txt").write_text("RHAISTRAT-9001\n")
    (workdir / "tmp/pipeline-batch-2-ids.txt").write_text("RHAISTRAT-9002\n")
    state = {"phase": "BATCH_START", "batch": 1, "revise_cycle": 2}
    advance(state)
    advance({**state, "phase": "REVIEW_DECOMP"})
    summary = workdir / "artifacts/epic-tasks/RHAISTRAT-9002-decomposition.md"
    data, _ = read_frontmatter(str(summary))
    write_md(summary, {**data, "revised": False})
    assert advance({**state, "phase": "RE_REVIEW_CHECK"})[0] == "RE_REVIEW"
    assert (workdir / "tmp/pipeline-revise-ids.txt").read_text().split() == ["RHAISTRAT-9002"]
    assert (workdir / "artifacts/epic-reviews/RHAISTRAT-9001-decomp-review.md").exists()


def test_repeated_no_edit_failures_exhaust_quality_budget(workdir):
    setup_strategy(workdir)
    state = {"phase": "RE_REVIEW_CHECK", "revise_cycle": 0}
    review = workdir / "artifacts/epic-reviews/RHAISTRAT-9001-decomp-review.md"
    summary = workdir / "artifacts/epic-tasks/RHAISTRAT-9001-decomposition.md"
    failing_review, _ = read_frontmatter(str(review))
    for attempt in range(3):
        assert advance(state)[0] == "RE_REVIEW"
        write_md(review, failing_review)  # Fresh reviewer still rejects the artifacts.
        state["phase"] = "REVISE_CHECK"
        nxt, _ = advance(state)
        if attempt == 2:
            assert nxt == "BATCH_DONE"
        else:
            assert nxt == "RE_REVISE"
            data, _ = read_frontmatter(str(summary))
            write_md(summary, {**data, "revised": False})
            state["phase"] = "RE_REVIEW_CHECK"
    assert state["revise_cycle"] == 2
    assert len(list((workdir / "artifacts/epic-reviews/history/RHAISTRAT-9001")
                    .glob("*.md"))) == 3
    assert _check_review_passed("artifacts", "RHAISTRAT-9001")[0] is False


@pytest.mark.parametrize("phase", ["BATCH_START", "REVIEW_DECOMP", "RE_REVIEW_CHECK", "REVISE_CHECK"])
def test_dry_run_does_not_mutate_state_or_artifacts(workdir, phase):
    setup_strategy(workdir)
    (workdir / "tmp/pipeline-batch-1-ids.txt").write_text("RHAISTRAT-9001\n")
    state = {"phase": phase, "batch": 0, "revise_cycle": 0}
    before_state = state.copy()
    before_files = {str(p): p.read_bytes() for p in workdir.rglob("*") if p.is_file()}
    advance(state, dry_run=True)
    assert state == before_state
    assert {str(p): p.read_bytes() for p in workdir.rglob("*") if p.is_file()} == before_files
