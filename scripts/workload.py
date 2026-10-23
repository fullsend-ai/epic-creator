"""Validated, repository-owned workload descriptors for strategy intake.

Descriptors bind intake identity and reviewed, repository-owned planning
policies. They never load executable plugins.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
from pathlib import Path
import re

from jsonschema import Draft202012Validator
import yaml

WORKLOADS_DIR = Path(__file__).resolve().parents[1] / "workloads"


class WorkloadError(ValueError):
    """Invalid descriptor, tracker identity or workload provenance."""


@dataclass(frozen=True)
class PlanningPolicy:
    decompose: Path
    review: Path
    revise: Path
    scoring: Path
    artifact_template: Path
    component_project: str
    component_file: str
    criteria: tuple
    points_per_criterion: int
    pass_score: int
    zero_fails: bool
    digest: str
    implementation_high: int
    implementation_medium: int
    investigation_high: int
    investigation_low: int

    @property
    def max_score(self):
        return len(self.criteria) * self.points_per_criterion

    def canonical_components(self):
        """Require the selected workload's component cache before producing plans."""
        path = WORKLOADS_DIR.parent / self.component_file
        try:
            names = {line.strip() for line in path.read_text().splitlines() if line.strip()}
        except OSError as exc:
            raise WorkloadError(
                f"Cannot read {path}; run scripts/fetch_components.py for this workload") from exc
        if not names:
            raise WorkloadError(f"Empty {path}; run scripts/fetch_components.py for this workload")
        return names

    def validate_component(self, name, names=None):
        names = self.canonical_components() if names is None else names
        if name not in names:
            raise WorkloadError(
                f"Unknown {self.component_project} component {name!r}; "
                f"choose an exact canonical name: {', '.join(sorted(names))}")

    def review_result(self, scores):
        if (not isinstance(scores, list) or len(scores) != len(self.criteria)
                or any(type(s) is not int or not 0 <= s <= self.points_per_criterion for s in scores)):
            raise WorkloadError("Invalid review criterion scores for the selected policy")
        total = sum(scores)
        return total, total >= self.pass_score and not (self.zero_fails and 0 in scores)

    def validate_review(self, data):
        if data.get("error"):
            if data.get("pass") is not False or data.get("recommendation") != "revise":
                raise WorkloadError("An error review cannot pass")
            return
        total, passed = self.review_result(data.get("criterion_scores"))
        if (type(data.get("score")) is not int or data["score"] != total
                or data.get("pass") is not passed
                or data.get("recommendation") != ("accept" if passed else "revise")):
            raise WorkloadError("Review result conflicts with selected gate")
        for issue in data.get("issues", []):
            if (not isinstance(issue, dict) or issue.get("severity") not in ("critical", "major", "minor")
                    or issue.get("criterion") not in self.criteria):
                raise WorkloadError("Review issue does not match the selected rubric")
            if issue["severity"] in ("critical", "major") and data["criterion_scores"][self.criteria.index(issue["criterion"])] != 0:
                raise WorkloadError("Critical or major review issues require a zero criterion score")


def _load_planning(data):
    if data is None:
        return None
    root = WORKLOADS_DIR.parent.resolve()
    paths, contents = {}, {}
    for stage in ("decompose", "review", "revise", "scoring", "artifact_template"):
        reference = Path(data[stage])
        path = (root / reference).resolve()
        if (reference.is_absolute() or ".." in reference.parts
                or not path.is_relative_to(root / "workloads" / "policies")
                or path.suffix != (".yaml" if stage == "scoring" else ".md") or not path.is_file()):
            raise WorkloadError(f"Unsafe or missing {stage} policy: {reference}")
        paths[stage] = path
        contents[stage] = path.read_text(encoding="utf-8")
    scoring = yaml.safe_load(contents["scoring"])
    scoring_schema = json.loads((WORKLOADS_DIR / "_schema/scoring.schema.json").read_text())
    errors = list(Draft202012Validator(scoring_schema).iter_errors(scoring))
    if errors:
        raise WorkloadError(f"Invalid scoring policy: {errors[0].message}")
    implementation, investigation = scoring["implementation"], scoring["investigation"]
    if (implementation["high_min"] <= implementation["medium_min"]
            or investigation["high_min"] <= investigation["low_max"]):
        raise WorkloadError("Scoring policy thresholds must be ordered")
    gate = data["review_gate"]
    if gate["pass_score"] > len(gate["criteria"]) * gate["points_per_criterion"]:
        raise WorkloadError("Review pass score exceeds the rubric maximum")
    digest = hashlib.sha256(json.dumps({"planning": data, "contents": contents},
                           sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return PlanningPolicy(**paths, component_project=data["components"]["project"],
                          component_file=data["components"]["file"],
                          criteria=tuple(gate["criteria"]), points_per_criterion=gate["points_per_criterion"],
                          pass_score=gate["pass_score"], zero_fails=gate["zero_fails"], digest=digest,
                          implementation_high=implementation["high_min"], implementation_medium=implementation["medium_min"],
                          investigation_high=investigation["high_min"], investigation_low=investigation["low_max"])


def _tracker_binding(issue):
    fields = issue.get("fields") if isinstance(issue, dict) else None
    if not isinstance(fields, dict):
        raise WorkloadError("Missing Jira source fields")
    project, issue_type = fields.get("project"), fields.get("issuetype")
    if not isinstance(project, dict) or not isinstance(issue_type, dict):
        raise WorkloadError("Missing Jira source project or issue type")
    return fields, (project.get("key"), issue_type.get("name"))


@dataclass(frozen=True)
class SubmissionPolicy:
    project: str
    issue_type: str
    parent: dict
    dependency_link_type: str
    labels: dict


@dataclass(frozen=True)
class Workload:
    id: str
    version: int
    project: str
    issue_type: str
    id_pattern: str
    attachment_template: str
    description_fallback: bool
    ready_labels: tuple
    skip_policy: str
    processed_label: str
    handoff_type: str
    identity_fields: tuple
    digest: str
    planning: PlanningPolicy | None = None
    submission: SubmissionPolicy | None = None

    def validate_identity(self, key, project, issue_type):
        if (project, issue_type) != (self.project, self.issue_type):
            raise WorkloadError(f"{self.id}: source project/issue type does not match")
        if (not isinstance(key, str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", key)
                or not re.fullmatch(self.id_pattern, key)):
            raise WorkloadError(f"{self.id}: invalid source key {key!r}")

    def validate_ready(self, issue):
        fields, binding = _tracker_binding(issue)
        self.validate_identity(issue.get("key"), *binding)
        labels = fields.get("labels") or []
        if not all(label in labels for label in self.ready_labels):
            raise WorkloadError(f"{issue.get('key')}: missing strategy approval labels")

    def validate_handoff(self, data, key):
        for name in self.identity_fields:
            if name in data and data[name] != key:
                raise WorkloadError(f"{key}: attachment {name} conflicts with source identity")
        if "type" in data and data["type"] != self.handoff_type:
            raise WorkloadError(f"{key}: attachment type conflicts with tracker binding")
        for name, expected in (("workload_id", self.id), ("workload_version", self.version),
                               ("workload_digest", self.digest)):
            if name in data and data[name] != expected:
                raise WorkloadError(f"{key}: attachment {name} conflicts with selected descriptor")


def load_registry(directory=None):
    """Load and validate every descriptor; reject duplicate IDs/bindings."""
    directory = Path(directory) if directory is not None else WORKLOADS_DIR
    schema = json.loads((WORKLOADS_DIR / "_schema/workload.schema.json").read_text())
    validator = Draft202012Validator(schema)
    registry, bindings = {}, set()
    try:
        paths = sorted(directory.glob("*.yaml"))
        if not paths:
            raise WorkloadError(f"No workload descriptors in {directory}")
        for path in paths:
            data = yaml.safe_load(path.read_text())
            errors = list(validator.iter_errors(data))
            if errors:
                raise WorkloadError(f"{path.name}: {errors[0].message}")
            source = data["source"]
            pattern = source["id_pattern"]
            if not pattern.startswith("^") or not pattern.endswith("$"):
                raise WorkloadError(f"{path.name}: id_pattern must be anchored")
            re.compile(pattern)
            digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            selected = Workload(data["id"], data["version"], digest=digest,
                                planning=_load_planning(data.get("planning")),
                                submission=SubmissionPolicy(**data["submission"]) if "submission" in data else None,
                                **{**source, "ready_labels": tuple(source["ready_labels"]),
                                   "identity_fields": tuple(source["identity_fields"])})
            binding = (selected.project, selected.issue_type)
            if selected.id in registry or binding in bindings:
                raise WorkloadError(f"{path.name}: duplicate workload ID or tracker binding")
            registry[selected.id] = selected
            bindings.add(binding)
    except (OSError, yaml.YAMLError, re.error) as exc:
        raise WorkloadError(f"Unable to load workload descriptors: {exc}") from exc
    return registry


def get_workload(workload_id, registry=None):
    registry = load_registry() if registry is None else registry
    try:
        return registry[workload_id]
    except (KeyError, TypeError) as exc:
        raise WorkloadError(f"Unknown workload {workload_id!r}") from exc


def resolve_issue(issue, workload_id=None, registry=None):
    """Resolve by Jira project + issue type, then verify the source key.

    An explicit selection constrains the binding; it cannot override it.
    Key prefixes alone never select a workload.
    """
    registry = load_registry() if registry is None else registry
    _, binding = _tracker_binding(issue)
    candidates = [w for w in registry.values()
                  if (w.project, w.issue_type) == binding]
    if len(candidates) != 1:
        raise WorkloadError(f"Unknown or ambiguous tracker binding {binding!r}")
    selected = candidates[0]
    if workload_id is not None and selected.id != workload_id:
        raise WorkloadError(f"Selected workload {workload_id!r} conflicts with {binding!r}")
    selected.validate_identity(issue.get("key"), *binding)
    return selected
