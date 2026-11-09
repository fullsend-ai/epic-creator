import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import submission_manifest


class TestSubmissionManifest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.strategy_id = "RHAISTRAT-1001"
        self.review_path = os.path.join(self.temp_dir.name, "review.md")
        with open(self.review_path, "w") as review:
            review.write("---\npass: true\nscore: 14\n---\n")
        self.plan = [
            {
                "epic_id": "RHAISTRAT-1001-E001",
                "title": "First epic",
                "priority": "Major",
                "component": "Platform",
                "labels": ["epic-creator-auto-created"],
                "dependencies": [],
                "path": os.path.join(self.temp_dir.name, "E001.md"),
            },
            {
                "epic_id": "RHAISTRAT-1001-E002",
                "title": "Second epic",
                "priority": "Minor",
                "component": None,
                "labels": ["epic-creator-auto-created"],
                "dependencies": ["RHAISTRAT-1001-E001"],
                "path": os.path.join(self.temp_dir.name, "E002.md"),
            },
        ]
        for entry in self.plan:
            with open(entry["path"], "w") as epic:
                epic.write("---\nepic_id: " + entry["epic_id"] + "\n---\n")

    def test_builds_valid_manifest_with_stable_run_id(self):
        first = submission_manifest.build_manifest(
            self.strategy_id, self.plan, {"pass": True, "score": 14}, self.review_path)
        second = submission_manifest.build_manifest(
            self.strategy_id, self.plan, {"pass": True, "score": 14}, self.review_path)
        submission_manifest.validate_manifest(first)
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertTrue(first["run_id"].startswith(self.strategy_id + "-"))

    def test_jira_key_does_not_change_approved_content_digest(self):
        first = submission_manifest.build_manifest(
            self.strategy_id, self.plan, {"pass": True, "score": 14},
            self.review_path)
        with open(self.plan[0]["path"]) as source:
            content = source.read()
        with open(self.plan[0]["path"], "w") as epic:
            epic.write(content.replace(
                "---\n", "---\njira_key: RHAI-100\n", 1))
        second = submission_manifest.build_manifest(
            self.strategy_id, self.plan, {"pass": True, "score": 14},
            self.review_path)
        self.assertEqual(first["epics"][0]["source_sha256"],
                         second["epics"][0]["source_sha256"])
        self.assertEqual(first["run_id"], second["run_id"])

    def test_branch_plan_content_is_part_of_manifest(self):
        branch_path = os.path.join(self.temp_dir.name,
                                   "RHAISTRAT-1001-BRANCH-A-E003.md")
        with open(branch_path, "w") as branch:
            branch.write(
                "---\n"
                "epic_id: RHAISTRAT-1001-BRANCH-A-E003\n"
                "branch: A\n"
                "gated_by: RHAISTRAT-1001-E001\n"
                "---\n"
                "## Description\n\nApproved branch content.\n")
        branches = {("A", "RHAISTRAT-1001-E001"): [
            (branch_path, {
                "epic_id": "RHAISTRAT-1001-BRANCH-A-E003",
                "branch": "A",
                "gated_by": "RHAISTRAT-1001-E001",
            }, "body"),
        ]}
        first = submission_manifest.build_manifest(
            self.strategy_id, self.plan, {"pass": True, "score": 14},
            self.review_path, branches=branches)
        self.assertEqual(len(first["branch_plans"]), 1)
        with open(branch_path, "a") as branch:
            branch.write("Changed content.\n")
        second = submission_manifest.build_manifest(
            self.strategy_id, self.plan, {"pass": True, "score": 14},
            self.review_path, branches=branches)
        self.assertNotEqual(first["branch_plans"][0]["source_sha256"],
                            second["branch_plans"][0]["source_sha256"])
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_rejects_unapproved_review(self):
        with self.assertRaises(submission_manifest.SubmissionManifestError):
            submission_manifest.build_manifest(
                self.strategy_id, self.plan, {"pass": False, "score": 5}, self.review_path)

    def test_rejects_unknown_dependency(self):
        self.plan[1]["dependencies"] = ["RHAISTRAT-1001-E999"]
        with self.assertRaises(submission_manifest.SubmissionManifestError):
            submission_manifest.build_manifest(
                self.strategy_id, self.plan, {"pass": True, "score": 14}, self.review_path)

    def test_rejects_duplicate_epic_id(self):
        self.plan[1]["epic_id"] = self.plan[0]["epic_id"]
        with self.assertRaises(submission_manifest.SubmissionManifestError):
            submission_manifest.build_manifest(
                self.strategy_id, self.plan, {"pass": True, "score": 14}, self.review_path)

    def test_rejects_dependency_cycle(self):
        self.plan[0]["dependencies"] = ["RHAISTRAT-1001-E002"]
        self.plan[1]["dependencies"] = ["RHAISTRAT-1001-E001"]
        with self.assertRaises(submission_manifest.SubmissionManifestError):
            submission_manifest.build_manifest(
                self.strategy_id, self.plan, {"pass": True, "score": 14}, self.review_path)

    def test_manifest_round_trip(self):
        manifest = submission_manifest.build_manifest(
            self.strategy_id, self.plan, {"pass": True, "score": 14}, self.review_path)
        path = os.path.join(self.temp_dir.name, "manifest.yaml")
        submission_manifest.write_manifest(path, manifest)
        loaded = submission_manifest.read_manifest(path)
        self.assertEqual(manifest, loaded)

    def test_invalid_manifest_is_not_written(self):
        manifest = submission_manifest.build_manifest(
            self.strategy_id, self.plan, {"pass": True, "score": 14}, self.review_path)
        manifest["approved"] = False
        path = os.path.join(self.temp_dir.name, "invalid.yaml")
        with self.assertRaises(submission_manifest.SubmissionManifestError):
            submission_manifest.write_manifest(path, manifest)
        self.assertFalse(os.path.exists(path))

    def test_malformed_manifest_is_reported_as_manifest_error(self):
        path = os.path.join(self.temp_dir.name, "malformed.yaml")
        with open(path, "w") as source:
            source.write("approved: [unclosed\n")
        with self.assertRaises(submission_manifest.SubmissionManifestError):
            submission_manifest.read_manifest(path)

    def test_manifest_path_is_exported(self):
        path = submission_manifest.manifest_path("artifacts", self.strategy_id)
        self.assertEqual(
            path,
            os.path.join("artifacts", "submission-manifests",
                         f"{self.strategy_id}.yaml"))


if __name__ == "__main__":
    unittest.main()
