"""Fixed RHAI configuration binding and repository artifact checks.

Repository-enabled stages refresh configured Git evidence. No Jira/model client.
"""
from copy import deepcopy
from pathlib import Path
import json
import hashlib
import yaml

from artifact_utils import _REPOSITORY_SOURCE_FIELDS, read_frontmatter, read_frontmatter_validated, ValidationError
import rhai_config
from rhai_config import RepositoryError

BOUND_FIELDS = {"rhai_config_digest", "planning_digest", "source_bindings", "repository_bindings"}


def bound_config(state):
    selected = rhai_config.load_config()
    if "rhai_config_digest" in state and (state["rhai_config_digest"] != selected.digest or state.get("planning_digest") != selected.planning_digest):
        raise RepositoryError("Bound RHAI repository configuration or prompts changed; start a new run")
    return selected


def phase_config(state, base):
    selected = bound_config(state)
    config = deepcopy(base)
    if config.get("type") == "agent" and selected.repository_policy:
        config.setdefault("vars", {}).update(
            REPOSITORY_PLAN="artifacts/repository-plans/{ID}.json",
            REPOSITORY_CONTEXT="artifacts/repository-plans/{ID}-context.json",
            COMPONENT_FILE=rhai_config.COMPONENT_FILE)
    return config


def agent_variables(state, key, variables):
    result = {name: value.replace("{ID}", key) for name, value in variables.items()}
    if state["phase"] != "FETCH" and bound_config(state).repository_policy:
        from repository_plan import paths
        result["MAPPING_SNAPSHOT_DIGEST"] = _json(paths("artifacts", key)["context"])["digest"]
    return result


def _json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise RepositoryError(f"Missing or invalid repository artifact: {path}") from exc


def repository_plan_for(state, key, artifacts_dir="artifacts", prepare=False):
    from repository_context import assert_context_current, load_repository_context
    from repository_plan import atomic_json, finalize_repository_plan, paths, validate_repository_plan
    selected = bound_config(state)
    locations = paths(artifacts_dir, key)
    source = Path(artifacts_dir) / f"strat-tasks/{key}.md"
    if prepare and not locations["context"].exists():
        context = load_repository_context(selected, source, Path(artifacts_dir))
        atomic_json(locations["context"], context)
        if context["status"] != "ready":
            atomic_json(locations["plan"], finalize_repository_plan(context, {}))
    context = _json(locations["context"])
    assert_context_current(context, selected, source)
    binding = state.get("repository_bindings", {}).get(key)
    if binding is not None and binding != context["digest"]:
        raise RepositoryError("Repository mapping binding changed during this run")
    if prepare:
        return context
    plan = _json(locations["plan"])
    validate_repository_plan(plan, context)
    return plan


def validate_ids(state, ids):
    selected = bound_config(state)
    for key in ids:
        selected.validate_identity(key)


def _read(path, schema):
    try:
        return read_frontmatter_validated(path, schema)[0]
    except (OSError, ValidationError, ValueError, yaml.YAMLError) as exc:
        raise RepositoryError(f"Invalid pipeline artifact {path}: {exc}") from exc


def source_metadata(state, key, artifacts_dir="artifacts"):
    selected = bound_config(state)
    validate_ids(state, [key])
    data = _read(Path(artifacts_dir) / f"strat-tasks/{key}.md", "strat-task")
    if data["strat_id"] != key:
        raise RepositoryError(f"{key}: source artifact identity conflicts with filename")
    expected = {"rhai_config_digest": selected.digest, "planning_digest": selected.planning_digest}
    if any(data.get(name) != value for name, value in expected.items()):
        raise RepositoryError(f"{key}: source configuration provenance conflicts with pipeline")
    if not data.get("source_revision"):
        raise RepositoryError(f"{key}: source revision is missing")
    body = read_frontmatter(Path(artifacts_dir) / f"strat-tasks/{key}.md")[1].strip()
    if hashlib.sha256(body.encode()).hexdigest() != data["source_revision"]:
        raise RepositoryError(f"{key}: source revision changed; fetch the approved strategy again")
    metadata = {name: data[name] for name in _REPOSITORY_SOURCE_FIELDS if name in data}
    binding = state.get("source_bindings", {}).get(key)
    if binding is not None and binding != metadata:
        raise RepositoryError(f"{key}: source revision changed during this run")
    return metadata


def validate_artifact(state, key, path, schema, artifacts_dir="artifacts", *, require_current_review=True):
    source = source_metadata(state, key, artifacts_dir)
    data = _read(path, schema)
    identity = "parent_strat" if schema in ("decomp-summary", "epic-task") else "strat_id"
    if data.get(identity) != key:
        raise RepositoryError(f"{key}: generated source identity conflicts at {path}")
    metadata = {name: data[name] for name in _REPOSITORY_SOURCE_FIELDS if name in data}
    if metadata != source:
        raise RepositoryError(f"{key}: generated provenance conflicts with strategy at {path}")
    if schema == "epic-task" and data["epic_id"] != Path(path).stem:
        raise RepositoryError(f"{key}: epic identity conflicts with filename at {path}")
    if schema == "decomp-review" and bound_config(state).repository_policy:
        from repository_plan import decomposition_digest
        plan = repository_plan_for(state, key, artifacts_dir)
        if (data.get("repository_plan_digest") != plan["digest"]
                or (require_current_review and data.get("reviewed_decomposition_digest") != decomposition_digest(plan, Path(artifacts_dir), key))):
            raise RepositoryError("Review no longer matches repository plan or decomposition content")
    return data


def validate_decomposition(state, key, artifacts_dir="artifacts"):
    directory = Path(artifacts_dir) / "epic-tasks"
    summary = validate_artifact(state, key, directory / f"{key}-decomposition.md", "decomp-summary", artifacts_dir)
    main = sorted(directory.glob(f"{key}-E[0-9][0-9][0-9].md"))
    branches = sorted(directory.glob(f"{key}-BRANCH-*-E[0-9][0-9][0-9].md"))
    if not main or summary["epic_count"] not in {len(main), len(main) + len(branches)}:
        raise RepositoryError(f"{key}: summary epic count does not match generated files")
    epics = [validate_artifact(state, key, path, "epic-task", artifacts_dir) for path in main + branches]
    if bound_config(state).repository_policy:
        from repository_plan import validate_epic_scopes
        plan = repository_plan_for(state, key, artifacts_dir)
        if summary.get("mapping_snapshot_digest") != plan["mapping_snapshot_digest"]:
            raise RepositoryError("Decomposition summary has wrong repository mapping snapshot")
        validate_epic_scopes(plan, epics)


def validate_completed(state, phase, ids):
    validate_ids(state, ids)
    for key in ids:
        metadata = source_metadata(state, key)
        if phase == "FETCH":
            state.setdefault("source_bindings", {})[key] = metadata
        elif phase == "PLAN_REPOSITORIES":
            plan = repository_plan_for(state, key)
            state.setdefault("repository_bindings", {})[key] = plan["mapping_snapshot_digest"]
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
        if bound_config(state).repository_policy:
            repository_plan_for(state, key, prepare=phase == "PLAN_REPOSITORIES")
        if phase in ("REVIEW_DECOMP", "RE_REVIEW", "REVISE_DECOMP", "RE_REVISE"):
            validate_decomposition(state, key)
        if phase in ("REVISE_DECOMP", "RE_REVISE"):
            from check_decompose_progress import check_id
            # A finished revision intentionally invalidates the prior content digest.
            # Keep source/plan checks; RE_REVIEW_CHECK will require a fresh review.
            completed = check_id("revise_decomp", key) == "completed"
            validate_artifact(state, key, f"artifacts/epic-reviews/{key}-decomp-review.md", "decomp-review",
                              require_current_review=not completed)
