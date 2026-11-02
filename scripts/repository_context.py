"""Canonical contribution inventory and content-bound repository evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import yaml

from repository_sources import fetch_git_sources, guidance_reference, normalize_git_source
from rhai_config import RepositoryError

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "config/_schema"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_inventory(data):
    schema = json.loads((SCHEMA_DIR / "repository-inventory.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(data))
    if errors:
        raise RepositoryError(f"Invalid contribution inventory: {errors[0].message}")
    inventory, labels, identities, aliases = {}, set(), set(), {}
    for entry in data["repositories"]:
        key = entry["inventory_key"]
        identity = f"{entry['host'].lower()}/{entry['namespace']}/{entry['repository']}"
        if key in inventory or entry["label"] in labels or identity in identities:
            raise RepositoryError("Duplicate repository inventory key, label or canonical identity")
        if entry["label"] != f"target-repo-{key}":
            raise RepositoryError("Repository label must match its inventory key")
        inventory[key] = entry
        labels.add(entry["label"])
        identities.add(identity)
        for alias in [identity, *entry.get("aliases", [])]:
            if alias in aliases and aliases[alias] != key:
                raise RepositoryError("Conflicting repository aliases")
            aliases[alias] = key
    return inventory


def source_binding(manifests):
    return [{**{k: v for k, v in source.items() if k not in ("files", "fetched_at")},
             "files": {name: {"sha256": content["sha256"]} for name, content in source["files"].items()}}
            for source in manifests]


def _binding(context):
    fields = ("schema_version", "source_key", "rhai_config_id", "rhai_config_version",
              "rhai_config_digest", "planning_digest", "strategy_sha256", "policy")
    binding = {**{key: context[key] for key in fields},
               "sources": source_binding(context["sources"]), "inventory": context["inventory_document"]}
    if "guidance" in context:
        binding["guidance"] = context["guidance"]
    return binding


def _load_guidance(policy, sources):
    reference = guidance_reference(policy)
    if reference is None:
        return None
    source = next(source for source in sources if source["id"] == reference["source_id"])
    file = source["files"][reference["path"]]
    content = Path(file["local_path"]).read_bytes()
    text = content.decode("utf-8")
    if not text.strip():
        raise RepositoryError("Repository guidance is empty")
    return {**reference, "sha256": hashlib.sha256(content).hexdigest(), "content": text}


def validate_context_snapshot(context):
    if context.get("status") != "ready":
        raise RepositoryError("Repository context is unresolved")
    if (digest(_binding(context)) != context["digest"]
            or validate_inventory(context["inventory_document"]) != context["inventory"]):
        raise RepositoryError("Repository context content changed")
    if hashlib.sha256(Path(context["strategy_path"]).read_bytes()).hexdigest() != context["strategy_sha256"]:
        raise RepositoryError("Repository strategy input changed")
    for source in context["sources"]:
        for file in source["files"].values():
            if hashlib.sha256(Path(file["local_path"]).read_bytes()).hexdigest() != file["sha256"]:
                raise RepositoryError("Repository evidence content changed")
    try:
        guidance = _load_guidance(context["policy"], context["sources"])
    except (RepositoryError, OSError, KeyError, StopIteration, UnicodeError):
        raise RepositoryError("Repository guidance content changed or became unavailable") from None
    if guidance != context.get("guidance"):
        raise RepositoryError("Repository guidance content changed")


def load_repository_context(selected, source_path: Path, artifacts_dir: Path) -> dict:
    policy = selected.repository_policy
    if not policy:
        raise RepositoryError("RHAI configuration has no repository policy")
    # Validate references before recording diagnostics or copying configuration.
    for source in policy["sources"]:
        normalize_git_source(source)
    context = {"schema_version": 1, "source_key": Path(source_path).stem,
               "rhai_config_id": "rhai", "rhai_config_version": selected.version,
               "rhai_config_digest": selected.digest, "planning_digest": selected.planning_digest,
               "strategy_sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),
               "policy": policy, "cache_root": str((Path(artifacts_dir) / "repository-sources").resolve())}
    try:
        sources = fetch_git_sources(policy, Path(context["cache_root"]))
        inventory_ref = policy["inventory"]
        source = next(s for s in sources if s["id"] == inventory_ref["source_id"])
        inventory_path = source["files"][inventory_ref["path"]]["local_path"]
        inventory_data = yaml.safe_load(Path(inventory_path).read_text())
        inventory = validate_inventory(inventory_data)
        guidance = _load_guidance(policy, sources)
    except (RepositoryError, OSError, KeyError, StopIteration, UnicodeError, yaml.YAMLError):
        return {**context, "status": "blocked", "reason": "source_unavailable",
                "inventory": {}, "sources": [], "digest": digest({**context, "cache_root": None})}
    result = {**context, "status": "ready", "sources": sources, "inventory": inventory,
              "strategy_path": str(Path(source_path).resolve()), "inventory_document": inventory_data,
              "inventory_revision": inventory_data["revision"]}
    if guidance is not None:
        result["guidance"] = guidance
    result["digest"] = digest(_binding(result))
    return result


def assert_context_current(context: dict, selected, source_path: Path) -> None:
    validate_context_snapshot(context)
    for source in context["sources"]:
        if source.get("max_age_seconds") is not None:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(source["fetched_at"])).total_seconds()
            if age > source["max_age_seconds"]:
                raise RepositoryError("Repository evidence freshness expired; plan again")
    current = load_repository_context(selected, source_path, Path(context["cache_root"]).parent)
    if current.get("status") != "ready" or current["digest"] != context["digest"]:
        raise RepositoryError("Repository mapping inputs changed or became unavailable; plan and review again")
