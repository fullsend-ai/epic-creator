"""The reviewer inventory reads ignored paths and includes conditional epics."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from check_review_files import check_review_files

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_review_files.py"
ID = "RHAISTRAT-9001"


def setup_files(root):
    directory = root / "artifacts/epic-tasks"
    directory.mkdir(parents=True)
    (root / ".gitignore").write_text("artifacts/\n")
    (directory / f"{ID}-decomposition.md").write_text(
        "---\nepic_count: 1\n---\n## Epic List\n| ID | Title |\n"
        "| E001 | Main |\n| BRANCH-A-E002 | Conditional |\n")
    for name in ("E001", "BRANCH-A-E002"):
        (directory / f"{ID}-{name}.md").write_text("Epic body\n")
        (directory / f"{ID}-{name}-ai-signals.md").write_text("Signal rationale\n")
    return directory


def test_reads_ignored_main_and_conditional_files(tmp_path, monkeypatch):
    setup_files(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = check_review_files(ID)
    assert result["errors"] == []
    assert len(result["epics"]) == 2
    assert all(entry["rationale_readable"] and entry["epic_readable"] for entry in result["epics"])


def test_missing_branch_and_rationale_are_reported(tmp_path):
    directory = setup_files(tmp_path)
    (directory / f"{ID}-BRANCH-A-E002.md").unlink()
    (directory / f"{ID}-E001-ai-signals.md").unlink()
    result = check_review_files(ID, tmp_path / "artifacts")
    assert len(result["epics"]) == 2
    assert any(f"{ID}-BRANCH-A-E002.md" in error for error in result["errors"])
    assert any(f"{ID}-E001-ai-signals.md" in error for error in result["errors"])
    entries = {Path(entry["epic_file"]).stem: entry for entry in result["epics"]}
    main = entries[f"{ID}-E001"]
    branch = entries[f"{ID}-BRANCH-A-E002"]
    assert (main["epic_readable"], main["rationale_readable"]) == (True, False)
    assert (branch["epic_readable"], branch["rationale_readable"]) == (False, True)


def test_unreadable_rationale_is_reported(tmp_path):
    directory = setup_files(tmp_path)
    unreadable = directory / f"{ID}-E001-ai-signals.md"
    read_text = Path.read_text
    def read(path, *args, **kwargs):
        if path == unreadable:
            raise PermissionError("denied")
        return read_text(path, *args, **kwargs)
    with patch.object(Path, "read_text", read):
        result = check_review_files(ID, tmp_path / "artifacts")
    assert any("denied" in error for error in result["errors"])


@pytest.mark.parametrize("summary", ["", "---\nepic_count: [broken\n---\n",
                                    "---\nepic_count: 3\n---\n"])
def test_incomplete_inventory_is_not_a_success(tmp_path, summary):
    directory = setup_files(tmp_path)
    (directory / f"{ID}-decomposition.md").write_text(summary)
    assert check_review_files(ID, tmp_path / "artifacts")["errors"]


def test_all_inclusive_branch_count_is_valid(tmp_path):
    directory = setup_files(tmp_path)
    path = directory / f"{ID}-decomposition.md"
    path.write_text(path.read_text().replace("epic_count: 1", "epic_count: 2"))
    assert check_review_files(ID, tmp_path / "artifacts")["errors"] == []


def test_cli_failure_and_repair_mode(tmp_path):
    directory = setup_files(tmp_path)
    (directory / f"{ID}-E001-ai-signals.md").write_text("")
    command = [sys.executable, str(SCRIPT), ID]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["errors"]
    result = subprocess.run(command + ["--allow-missing"], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)["errors"]


def test_rejects_path_input():
    with pytest.raises(ValueError):
        check_review_files("../../outside")
