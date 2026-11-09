import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import submission_ledger


class TestSubmissionLedger(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = os.path.join(self.temp_dir.name, "ledger.json")
        self.ledger = submission_ledger.SubmissionLedger(
            self.path, "RHAISTRAT-1001-run", "digest")

    def test_records_before_and_after_state(self):
        self.ledger.begin("create:E001", "create_epic", "RHAISTRAT-1001-E001",
                          before={"jira_key": None})
        self.ledger.success("create:E001", after={"jira_key": "RHAI-100"})
        entry = self.ledger.entry("create:E001")
        self.assertEqual(entry["status"], "succeeded")
        self.assertEqual(entry["before"], {"jira_key": None})
        self.assertEqual(entry["after"], {"jira_key": "RHAI-100"})
        self.assertTrue(self.ledger.has_succeeded("create:E001"))

    def test_records_failure_and_attempt_count(self):
        self.ledger.begin("link:E001:E002", "create_link", "RHAI-100")
        self.ledger.failure("link:E001:E002", "HTTP 500")
        self.ledger.begin("link:E001:E002", "create_link", "RHAI-100")
        entry = self.ledger.entry("link:E001:E002")
        self.assertEqual(entry["status"], "in_progress")
        self.assertEqual(entry["attempts"], 2)
        self.assertEqual(entry["error"], "HTTP 500")

    def test_persists_and_reloads_ledger(self):
        self.ledger.begin("label:strategy", "label_strategy", "RHAISTRAT-1001")
        self.ledger.success("label:strategy", after={"labels": ["done"]})
        reloaded = submission_ledger.SubmissionLedger(
            self.path, "RHAISTRAT-1001-run", "digest")
        self.assertTrue(reloaded.has_succeeded("label:strategy"))

    def test_rejects_manifest_digest_mismatch(self):
        self.ledger.begin("label:strategy", "label_strategy", "RHAISTRAT-1001")
        with self.assertRaises(submission_ledger.SubmissionLedgerError):
            submission_ledger.SubmissionLedger(
                self.path, "RHAISTRAT-1001-run", "different-digest")

    def test_lock_is_exclusive(self):
        def try_lock():
            with submission_ledger.submission_lock(
                    "RHAISTRAT-1001-run",
                    os.path.join(self.temp_dir.name, "locks")):
                pass

        with submission_ledger.submission_lock(
                "RHAISTRAT-1001-run",
                os.path.join(self.temp_dir.name, "locks")), \
             self.assertRaises(submission_ledger.SubmissionLockError):
            try_lock()


if __name__ == "__main__":
    unittest.main()
