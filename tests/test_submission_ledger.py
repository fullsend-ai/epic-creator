import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import submission_ledger


@pytest.fixture
def ledger(tmp_path):
    return submission_ledger.SubmissionLedger(
        tmp_path / "ledger.json", "RHAISTRAT-1001-run", "digest")


def test_records_before_and_after_state(ledger):
    ledger.begin("create:E001", "create_epic", "RHAISTRAT-1001-E001",
                 before={"jira_key": None})
    ledger.success("create:E001", after={"jira_key": "RHAI-100"})
    entry = ledger.entry("create:E001")
    assert entry["status"] == "succeeded"
    assert entry["before"] == {"jira_key": None}
    assert entry["after"] == {"jira_key": "RHAI-100"}
    assert ledger.has_succeeded("create:E001")


def test_records_failure_and_attempt_count(ledger):
    ledger.begin("link:E001:E002", "create_link", "RHAI-100")
    ledger.failure("link:E001:E002", "HTTP 500")
    ledger.begin("link:E001:E002", "create_link", "RHAI-100")
    entry = ledger.entry("link:E001:E002")
    assert entry["status"] == "in_progress"
    assert entry["attempts"] == 2
    assert entry["error"] == "HTTP 500"


def test_persists_and_reloads_ledger(ledger, tmp_path):
    ledger.begin("label:strategy", "label_strategy", "RHAISTRAT-1001")
    ledger.success("label:strategy", after={"labels": ["done"]})
    reloaded = submission_ledger.SubmissionLedger(
        tmp_path / "ledger.json", "RHAISTRAT-1001-run", "digest")
    assert reloaded.has_succeeded("label:strategy")


def test_rejects_manifest_digest_mismatch(ledger, tmp_path):
    ledger.begin("label:strategy", "label_strategy", "RHAISTRAT-1001")
    with pytest.raises(submission_ledger.SubmissionLedgerError):
        submission_ledger.SubmissionLedger(
            tmp_path / "ledger.json", "RHAISTRAT-1001-run",
            "different-digest")


def test_lock_is_exclusive(tmp_path):
    def try_lock():
        with submission_ledger.submission_lock(
                "RHAISTRAT-1001-run", tmp_path / "locks"):
            pass

    with submission_ledger.submission_lock(
            "RHAISTRAT-1001-run", tmp_path / "locks"), \
         pytest.raises(submission_ledger.SubmissionLockError):
        try_lock()


def test_rejects_malformed_ledger_entry(ledger, tmp_path):
    ledger.begin("create:E001", "create_epic",
                 "RHAISTRAT-1001-E001", before={"jira_key": None})
    payload = json.loads((tmp_path / "ledger.json").read_text())
    payload["entries"]["create:E001"]["attempts"] = "two"
    (tmp_path / "ledger.json").write_text(json.dumps(payload))

    with pytest.raises(submission_ledger.SubmissionLedgerError):
        submission_ledger.SubmissionLedger(
            tmp_path / "ledger.json", "RHAISTRAT-1001-run", "digest")
