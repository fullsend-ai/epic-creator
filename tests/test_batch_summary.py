from pathlib import Path
import sys

from repository_fixtures import write_artifact
import batch_summary
from pipeline_status import write_status


def test_invalid_status_overrides_existing_passing_review(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    key = "RHAISTRAT-9001"
    write_artifact(Path(f"artifacts/epic-tasks/{key}-decomposition.md"),
                   {"parent_strat": key, "epic_count": 2, "critical_path_length": 1},
                   "decomp-summary", body="Two drafted epics\n")
    write_artifact(Path(f"artifacts/epic-reviews/{key}-decomp-review.md"),
                   {"strat_id": key, "score": 13, "pass": True, "recommendation": "accept"},
                   "decomp-review", body="Passing old review\n")
    write_status(key, "invalid", "revise_decomp", "Epic dependencies conflict with repository scope plan")
    monkeypatch.setattr(sys, "argv", ["batch_summary.py", "--counts-only", key])
    batch_summary.main()
    result = capsys.readouterr().out
    assert "passed=0" in result
    assert "errors=1" in result
