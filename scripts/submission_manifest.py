"""Build and validate protected Jira submission manifests.

The manifest is the approval boundary between the decomposition artifacts and
the host-side Jira submitter.  It records the approved strategy, the expected
epic DAG, and hashes of the source artifacts that produced it.
"""

import hashlib
import json
import os
import re

import yaml

SCHEMA_VERSION = 1
STRATEGY_ID_PATTERN = re.compile(r"^RHAISTRAT-\d+$")
EPIC_ID_PATTERN = re.compile(r"^RHAISTRAT-\d+-E\d+$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PRIORITIES = {"Critical", "Major", "Minor"}


class SubmissionManifestError(ValueError):
    """A submission manifest is missing required or invalid data."""


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _manifest_digest(manifest):
    return hashlib.sha256(_canonical_json(manifest).encode()).hexdigest()


def manifest_digest(manifest):
    """Return a stable digest for a validated manifest."""
    return _manifest_digest(validate_manifest(manifest))


def _derive_run_id(manifest):
    payload = {key: value for key, value in manifest.items() if key != "run_id"}
    digest = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    return f"{manifest['strategy_id']}-{digest[:12]}"


def build_manifest(strategy_id, plan, review_data, review_path):
    """Build a submission manifest from an approved decomposition plan."""
    if not isinstance(strategy_id, str) or not STRATEGY_ID_PATTERN.fullmatch(strategy_id):
        raise SubmissionManifestError(f"Invalid strategy ID: {strategy_id!r}")
    if not isinstance(review_data, dict):
        raise SubmissionManifestError("Review data must be a mapping.")
    if review_data.get("pass") is not True:
        raise SubmissionManifestError("Submission requires a passing review.")
    if not isinstance(plan, list) or not plan:
        raise SubmissionManifestError("Submission requires at least one epic.")

    epics = []
    for entry in plan:
        if not isinstance(entry, dict):
            raise SubmissionManifestError("Each plan entry must be a mapping.")
        epic_id = entry.get("epic_id")
        title = entry.get("title")
        priority = entry.get("priority")
        component = entry.get("component")
        labels = entry.get("labels", [])
        dependencies = entry.get("dependencies", [])
        source_path = entry.get("path")

        if not isinstance(epic_id, str) or not EPIC_ID_PATTERN.fullmatch(epic_id):
            raise SubmissionManifestError(f"Invalid epic ID: {epic_id!r}")
        if not isinstance(title, str) or not title.strip():
            raise SubmissionManifestError(f"Epic {epic_id} requires a title.")
        if priority not in PRIORITIES:
            raise SubmissionManifestError(f"Epic {epic_id} has invalid priority {priority!r}.")
        if component is not None and not isinstance(component, str):
            raise SubmissionManifestError(f"Epic {epic_id} has an invalid component.")
        if not isinstance(labels, list) or not all(isinstance(label, str) and label for label in labels):
            raise SubmissionManifestError(f"Epic {epic_id} has invalid labels.")
        if not isinstance(dependencies, list) or not all(isinstance(dep, str) for dep in dependencies):
            raise SubmissionManifestError(f"Epic {epic_id} has invalid dependencies.")
        if not isinstance(source_path, str) or not source_path:
            raise SubmissionManifestError(f"Epic {epic_id} requires a source artifact path.")
        if not os.path.isfile(source_path):
            raise SubmissionManifestError(f"Epic {epic_id} source artifact not found: {source_path}")

        epics.append({
            "epic_id": epic_id,
            "title": title,
            "priority": priority,
            "component": component,
            "labels": list(labels),
            "dependencies": list(dependencies),
            "source_path": source_path,
            "source_sha256": _sha256_file(source_path),
        })

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": strategy_id,
        "approved": True,
        "approval": {
            "review_path": review_path,
            "review_score": review_data.get("score"),
            "review_sha256": _sha256_file(review_path),
        },
        "epics": epics,
        "expected_jira": {
            "project": "RHAI",
            "issue_type": "Epic",
            "parent_key": strategy_id,
        },
    }
    manifest["run_id"] = _derive_run_id(manifest)
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest):
    """Validate manifest structure and DAG integrity."""
    if not isinstance(manifest, dict):
        raise SubmissionManifestError("Manifest must be a mapping.")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise SubmissionManifestError(f"Unsupported manifest schema version: {manifest.get('schema_version')!r}")
    if not isinstance(manifest.get("run_id"), str) or not manifest["run_id"].strip():
        raise SubmissionManifestError("Manifest requires a run ID.")
    if manifest["run_id"] != _derive_run_id(manifest):
        raise SubmissionManifestError("Manifest run ID does not match its content.")
    strategy_id = manifest.get("strategy_id")
    if not isinstance(strategy_id, str) or not STRATEGY_ID_PATTERN.fullmatch(strategy_id):
        raise SubmissionManifestError(f"Invalid strategy ID: {strategy_id!r}")
    if manifest.get("approved") is not True:
        raise SubmissionManifestError("Manifest must be explicitly approved.")

    approval = manifest.get("approval")
    if not isinstance(approval, dict):
        raise SubmissionManifestError("Manifest requires approval metadata.")
    if not isinstance(approval.get("review_path"), str) or not approval["review_path"]:
        raise SubmissionManifestError("Approval metadata requires a review path.")
    if not isinstance(approval.get("review_score"), int):
        raise SubmissionManifestError("Approval metadata requires an integer review score.")
    if not isinstance(approval.get("review_sha256"), str) or not SHA256_PATTERN.fullmatch(approval["review_sha256"]):
        raise SubmissionManifestError("Approval metadata requires a valid review hash.")

    epics = manifest.get("epics")
    if not isinstance(epics, list) or not epics:
        raise SubmissionManifestError("Manifest requires at least one epic.")
    epic_ids = set()
    for epic in epics:
        if not isinstance(epic, dict):
            raise SubmissionManifestError("Each manifest epic must be a mapping.")
        epic_id = epic.get("epic_id")
        if not isinstance(epic_id, str) or not EPIC_ID_PATTERN.fullmatch(epic_id):
            raise SubmissionManifestError(f"Invalid epic ID: {epic_id!r}")
        if epic_id in epic_ids:
            raise SubmissionManifestError(f"Duplicate epic ID: {epic_id}")
        epic_ids.add(epic_id)
        if not isinstance(epic.get("title"), str) or not epic["title"].strip():
            raise SubmissionManifestError(f"Epic {epic_id} requires a title.")
        if epic.get("priority") not in PRIORITIES:
            raise SubmissionManifestError(f"Epic {epic_id} has invalid priority.")
        if not isinstance(epic.get("component"), (str, type(None))):
            raise SubmissionManifestError(f"Epic {epic_id} has invalid component.")
        if not isinstance(epic.get("labels"), list):
            raise SubmissionManifestError(f"Epic {epic_id} has invalid labels.")
        if not isinstance(epic.get("source_path"), str) or not epic["source_path"]:
            raise SubmissionManifestError(f"Epic {epic_id} requires a source path.")
        if not isinstance(epic.get("source_sha256"), str) or not SHA256_PATTERN.fullmatch(epic["source_sha256"]):
            raise SubmissionManifestError(f"Epic {epic_id} has invalid source hash.")

    for epic in epics:
        dependencies = epic.get("dependencies")
        if not isinstance(dependencies, list):
            raise SubmissionManifestError(f"Epic {epic['epic_id']} has invalid dependencies.")
        for dependency in dependencies:
            if dependency not in epic_ids:
                raise SubmissionManifestError(
                    f"Epic {epic['epic_id']} depends on unknown epic {dependency!r}.")

    _validate_acyclic(epics)

    expected_jira = manifest.get("expected_jira")
    if not isinstance(expected_jira, dict):
        raise SubmissionManifestError("Manifest requires expected Jira fields.")
    if expected_jira.get("project") != "RHAI":
        raise SubmissionManifestError("Manifest must target the RHAI project.")
    if expected_jira.get("issue_type") != "Epic":
        raise SubmissionManifestError("Manifest must create Epic issues.")
    if expected_jira.get("parent_key") != strategy_id:
        raise SubmissionManifestError("Manifest parent key must match the strategy ID.")

    return manifest


def _validate_acyclic(epics):
    dependency_map = {
        epic["epic_id"]: set(epic.get("dependencies", []))
        for epic in epics
    }
    visiting = set()
    visited = set()

    def visit(epic_id):
        if epic_id in visited:
            return
        if epic_id in visiting:
            raise SubmissionManifestError(f"Manifest dependency graph contains a cycle at {epic_id}.")
        visiting.add(epic_id)
        for dependency in dependency_map.get(epic_id, set()):
            visit(dependency)
        visiting.remove(epic_id)
        visited.add(epic_id)

    for epic_id in dependency_map:
        visit(epic_id)


def write_manifest(path, manifest):
    """Persist a validated manifest atomically."""
    validate_manifest(manifest)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temporary_path = f"{path}.tmp"
    with open(temporary_path, "w") as target:
        yaml.safe_dump(manifest, target, sort_keys=False, allow_unicode=True)
    os.replace(temporary_path, path)


def read_manifest(path):
    """Load and validate a manifest from disk."""
    with open(path) as source:
        manifest = yaml.safe_load(source)
    return validate_manifest(manifest)
