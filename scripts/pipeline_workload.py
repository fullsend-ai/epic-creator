"""Workload binding and artifact checks for the shared dispatcher.

This module performs local validation only. It has no Jira or model client.
"""
from copy import deepcopy
from pathlib import Path
import yaml

from artifact_utils import _WORKLOAD_FIELDS, read_frontmatter_validated, ValidationError
from workload import get_workload, WorkloadError

BOUND_FIELDS = {"workload_id", "workload_version", "workload_digest", "planning_digest",
                "workload_explicit", "source_bindings"}
STAGES = {"FETCH": "fetch", "DECOMPOSE": "decompose", "REVIEW_DECOMP": "review",
          "RE_REVIEW": "review", "REVISE_DECOMP": "revise", "RE_REVISE": "revise"}


def bound_workload(state):
    selected = get_workload(state.get("workload_id", "rhaistrat-strategy"))
    if selected.planning is None:
        raise WorkloadError(f"{selected.id}: no planning policy configured")
    if "workload_id" in state:
        if (type(state.get("workload_version")) is not int
                or state["workload_version"] != selected.version
                or state.get("workload_digest") != selected.digest):
            raise WorkloadError("Bound workload descriptor changed; start a new run")
        if state.get("planning_digest") != selected.planning.digest:
            raise WorkloadError("Bound planning policy changed; start a new run")
        if type(state.get("workload_explicit")) is not bool:
            raise WorkloadError("Missing explicit/legacy workload binding")
    return selected


def phase_config(state, base):
    selected = bound_workload(state)
    config = deepcopy(base)
    if config.get("type") == "agent":
        stage = STAGES[state["phase"]]
        variables = config.setdefault("vars", {})
        variables.update(WORKLOAD_ID=selected.id, COMPONENT_FILE=selected.planning.component_file,
                         ARTIFACT_TEMPLATE=str(selected.planning.artifact_template),
                         PASS_SCORE=str(selected.planning.pass_score), MAX_SCORE=str(selected.planning.max_score),
                         FETCH_OPTIONS=f"--workload {selected.id}" if state.get("workload_explicit") else "")
        if stage != "fetch":
            variables["POLICY_FILE"] = str(getattr(selected.planning, stage))
    return config


def validate_ids(state, ids):
    selected = bound_workload(state)
    for key in ids:
        selected.validate_identity(key, selected.project, selected.issue_type)


def _read(path, schema):
    try:
        return read_frontmatter_validated(path, schema)[0]
    except (OSError, ValidationError, ValueError, yaml.YAMLError) as exc:
        raise WorkloadError(f"Invalid pipeline artifact {path}: {exc}") from exc


def source_metadata(state, key, artifacts_dir="artifacts"):
    selected = bound_workload(state)
    validate_ids(state, [key])
    data = _read(Path(artifacts_dir) / f"strat-tasks/{key}.md", "strat-task")
    if data["strat_id"] != key:
        raise WorkloadError(f"{key}: source artifact identity conflicts with filename")
    if state.get("workload_explicit") or data.get("workload_id"):
        expected = dict(workload_id=selected.id, workload_version=selected.version,
                        workload_digest=selected.digest, planning_digest=selected.planning.digest)
        if any(data.get(name) != value for name, value in expected.items()):
            raise WorkloadError(f"{key}: source workload/policy provenance conflicts with pipeline")
        selected.validate_ready({"key": key, "fields": {
            "project": {"key": data["source_project"]},
            "issuetype": {"name": data["source_issue_type"]}, "labels": data.get("labels", [])}})
    metadata = {name: data[name] for name in _WORKLOAD_FIELDS if name in data}
    binding = state.get("source_bindings", {}).get(key)
    if binding is not None and binding != metadata:
        raise WorkloadError(f"{key}: source revision changed during this run")
    return metadata


def validate_artifact(state, key, path, schema, artifacts_dir="artifacts"):
    source = source_metadata(state, key, artifacts_dir)
    data = _read(path, schema)
    identity = "parent_strat" if schema in ("decomp-summary", "epic-task") else "strat_id"
    if data.get(identity) != key:
        raise WorkloadError(f"{key}: generated source identity conflicts at {path}")
    metadata = {name: data[name] for name in _WORKLOAD_FIELDS if name in data}
    if metadata != source:
        raise WorkloadError(f"{key}: generated provenance conflicts with strategy at {path}")
    if schema == "epic-task" and data["epic_id"] != Path(path).stem:
        raise WorkloadError(f"{key}: epic identity conflicts with filename at {path}")
    if schema == "decomp-review" and source:
        bound_workload(state).planning.validate_review(data)
    return data


def validate_decomposition(state, key, artifacts_dir="artifacts"):
    directory = Path(artifacts_dir) / "epic-tasks"
    summary = validate_artifact(state, key, directory / f"{key}-decomposition.md", "decomp-summary", artifacts_dir)
    main = sorted(directory.glob(f"{key}-E[0-9][0-9][0-9].md"))
    branches = sorted(directory.glob(f"{key}-BRANCH-*-E[0-9][0-9][0-9].md"))
    if not main or summary["epic_count"] not in {len(main), len(main) + len(branches)}:
        raise WorkloadError(f"{key}: summary epic count does not match generated files")
    for path in main + branches:
        validate_artifact(state, key, path, "epic-task", artifacts_dir)


def validate_completed(state, phase, ids):
    validate_ids(state, ids)
    for key in ids:
        metadata = source_metadata(state, key)
        if phase == "FETCH":
            state.setdefault("source_bindings", {})[key] = metadata
        elif phase in ("DECOMPOSE", "REVISE_DECOMP", "RE_REVISE"):
            validate_decomposition(state, key)
        elif phase in ("REVIEW_DECOMP", "RE_REVIEW"):
            validate_decomposition(state, key)
            validate_artifact(state, key, f"artifacts/epic-reviews/{key}-decomp-review.md", "decomp-review")


def validate_inputs(state, phase, ids):
    validate_ids(state, ids)
    if phase == "FETCH":
        return
    for key in ids:
        source_metadata(state, key)
        if phase in ("REVIEW_DECOMP", "RE_REVIEW", "REVISE_DECOMP", "RE_REVISE"):
            validate_decomposition(state, key)
        if phase in ("REVISE_DECOMP", "RE_REVISE"):
            validate_artifact(state, key, f"artifacts/epic-reviews/{key}-decomp-review.md", "decomp-review")
