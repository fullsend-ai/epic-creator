"""Resolve work into single-repository planned PRs before epic creation."""
from __future__ import annotations

import argparse
from collections import defaultdict
from graphlib import CycleError, TopologicalSorter
from itertools import product
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from jsonschema import Draft202012Validator

from repository_context import (SCHEMA_DIR, digest, load_repository_context,
                                validate_context_snapshot)
from rhai_config import RepositoryError, load_config

TARGET_FIELDS = ("inventory_key", "provider", "host", "namespace", "repository")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def _quote(quote, text):
    return bool(quote.strip()) and " ".join(quote.split()) in " ".join(text.split())


def _acyclic(graph):
    try:
        tuple(TopologicalSorter(graph).static_order())
        return True
    except CycleError:
        return False


def _evidence(resolution, sources, guidance):
    authoritative = False
    for citation in resolution.get("evidence_refs", []):
        source = sources.get(citation["source_id"])
        if source is None or citation["path"] not in source["files"]:
            raise RepositoryError("Repository citation references unconfigured source content")
        text = Path(source["files"][citation["path"]]["local_path"]).read_text()
        if not _quote(citation["quote"], text):
            raise RepositoryError("Repository evidence quote does not exist in bound source")
        is_guidance = guidance and (citation["source_id"], citation["path"]) == (guidance["source_id"], guidance["path"])
        authoritative |= source["role"] == "authoritative" and not is_guidance
    return authoritative


def _scenarios(scopes):
    outcomes = defaultdict(set)
    for scope in scopes:
        if scope.get("branch"):
            outcomes[scope["gated_by"]].add(scope["branch"])
    gates = sorted(outcomes)
    choices = product(*(sorted(outcomes[gate]) for gate in gates))
    for values in choices:
        scenario = dict(zip(gates, values))
        yield [scope for scope in scopes if not scope.get("branch")
               or scenario.get(scope["gated_by"]) == scope["branch"]]


def finalize_repository_plan(context: dict, proposal: dict) -> dict:
    if context.get("status") != "ready":
        payload = {"schema_version": 1, "source_key": context["source_key"],
                   "mapping_snapshot_digest": context["digest"], "proposal": proposal,
                   "requirements": [], "work_items": [], "scopes": [],
                   "unresolved": [{"work_item_id": None, "reason": context.get("reason", "source_unavailable"),
                                   "candidates": []}], "complete": True, "ready_for_drafting": False}
        return {**payload, "digest": digest(payload)}
    validate_context_snapshot(context)
    schema = json.loads((SCHEMA_DIR / "repository-plan.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(proposal))
    if errors:
        raise RepositoryError(f"Invalid repository proposal: {errors[0].message}")
    if (proposal["source_key"] != context["source_key"]
            or proposal["mapping_snapshot_digest"] != context["digest"]):
        raise RepositoryError("Repository proposal conflicts with source/snapshot binding")
    text = Path(context["strategy_path"]).read_text()
    requirements = {r["id"]: r for r in proposal["requirements"]}
    if len(requirements) != len(proposal["requirements"]):
        raise RepositoryError("Duplicate source requirement/acceptance IDs")
    if any(not _quote(r["quote"], text) for r in requirements.values()):
        raise RepositoryError("Source requirement quote is not in strategy")
    explicit_ids = set(re.findall(r"\b(?:HLR|AC)-\d+\b", text))
    if not explicit_ids <= requirements.keys():
        raise RepositoryError("Strategy requirement/acceptance catalog is incomplete")
    items = {item["id"]: item for item in proposal["work_items"]}
    if len(items) != len(proposal["work_items"]):
        raise RepositoryError("Duplicate work item IDs")
    coverage = set()
    for item in items.values():
        refs = item["requirement_refs"] + item["acceptance_refs"]
        if any(ref not in requirements for ref in refs):
            raise RepositoryError("Work item references unknown strategy requirement")
        coverage.update(refs)
        ac_refs = {ac["source_ref"] for ac in item["acceptance_criteria"]}
        if (not set(item["acceptance_refs"]) <= ac_refs
                or not ac_refs <= set(refs)):
            raise RepositoryError("Work item acceptance criteria do not cover assigned source ACs")
        if any(dep not in items or dep == item["id"] for dep in item["depends_on"]):
            raise RepositoryError("Unknown or self-referencing work dependency")
        if item.get("branch"):
            gate = items.get(item.get("gated_by"))
            if gate is None or gate["kind"] != "investigation" or gate["id"] not in item["depends_on"]:
                raise RepositoryError("Conditional work requires an Investigation dependency")
    deferred = {entry["id"] for entry in proposal.get("deferred", [])
                if entry["id"] in requirements and _quote(entry["source_quote"], text)}
    if coverage | deferred != requirements.keys():
        raise RepositoryError("Repository plan omits required strategy work or acceptance criteria")
    if not _acyclic({item["id"]: item["depends_on"] for item in items.values()}):
        raise RepositoryError("Work item dependency cycle")
    sources = {source["id"]: source for source in context["sources"]}
    inventory = context["inventory"]
    unresolved, destinations = [], {}
    for item in items.values():
        if item["kind"] == "investigation":
            continue
        resolution = item.get("resolution", {})
        key = resolution.get("target_key")
        reason = None
        if resolution.get("status") != "resolved":
            reason = resolution.get("reason", "repository_unresolved")
        elif key not in inventory:
            reason = "unknown_repository"
        elif not _evidence(resolution, sources, context.get("guidance")):
            reason = "missing_authoritative_evidence"
        if reason:
            candidates = resolution.get("candidates", [])
            if any(candidate not in inventory for candidate in candidates):
                raise RepositoryError("Unresolved candidates must belong to contribution inventory")
            unresolved.append({"work_item_id": item["id"], "reason": reason, "candidates": candidates,
                               "evidence_refs": resolution.get("evidence_refs", [])})
        else:
            destinations[item["id"]] = key
    aliases = {value: key for key, entry in inventory.items()
               for value in [key, f"{entry['host']}/{entry['namespace']}/{entry['repository']}",
                             f"https://{entry['host']}/{entry['namespace']}/{entry['repository']}",
                             *entry.get("aliases", [])]}
    for explicit in proposal.get("explicit_repositories", []):
        if not _quote(explicit["source_quote"], text):
            raise RepositoryError("Explicit repository reference is not supported by strategy text")
        expected = aliases.get(explicit["value"].removesuffix(".git").rstrip("/"))
        for wid in explicit["work_item_ids"]:
            if wid not in items:
                raise RepositoryError("Explicit repository input references unknown work")
            if expected is None or destinations.get(wid) != expected:
                unresolved.append({"work_item_id": wid, "reason": "explicit_repository_conflict",
                                   "candidates": [expected] if expected else []})
    groups = defaultdict(list)
    for item in items.values():
        if item["kind"] == "investigation":
            scope_id = f"investigation/{item['id']}"
        elif item["id"] not in destinations:
            continue
        else:
            scope_id = f"pr/{item['planned_pr']}"
        groups[scope_id].append(item)
    item_scope = {item["id"]: scope_id for scope_id, members in groups.items() for item in members}
    scopes = []
    for scope_id, members in sorted(groups.items()):
        first = members[0]
        if first["kind"] == "implementation":
            targets = {destinations[item["id"]] for item in members}
            if len(targets) != 1:
                raise RepositoryError("A planned PR must target a single repository")
            conditions = {(item.get("branch"), item.get("gated_by")) for item in members}
            if len(conditions) != 1:
                raise RepositoryError("A planned PR must have one consistent conditional branch/gate")
        components = list(dict.fromkeys(i["component"] for i in members))
        types = {i["delivery_type"] for i in members}
        scope = {"scope_id": scope_id, "kind": first["kind"],
                 "work_item_ids": [i["id"] for i in members], "components": components,
                 "teams": list(dict.fromkeys(i["team"] for i in members)),
                 "priority": min(i["priority"] for i in members),
                 "implementation_type": next(iter(types)) if len(types) == 1 and "general" not in types else None,
                 "dependencies": sorted({item_scope[dep] for i in members for dep in i["depends_on"]
                                         if dep in item_scope and item_scope[dep] != scope_id})}
        if first["kind"] == "implementation":
            entry = inventory[destinations[first["id"]]]
            scope["planned_pr"] = first["planned_pr"]
            scope["target_repository"] = {name: entry[name] for name in TARGET_FIELDS}
            scope["target_label"] = entry["label"]
            if first.get("branch"):
                scope.update(branch=first["branch"], gated_by=item_scope[first["gated_by"]])
        scopes.append(scope)
    for scenario in _scenarios(scopes):
        ids = {scope["scope_id"] for scope in scenario}
        if any(dep not in ids for scope in scenario for dep in scope["dependencies"]):
            unresolved.append({"work_item_id": None, "reason": "conditional_dependency_conflict", "candidates": []})
        elif not _acyclic({s["scope_id"]: s["dependencies"] for s in scenario}):
            unresolved.append({"work_item_id": None, "reason": "repository_dependency_cycle", "candidates": []})
    payload = {"schema_version": 1, "source_key": proposal["source_key"],
               "mapping_snapshot_digest": context["digest"], "proposal": proposal,
               "requirements": proposal["requirements"], "work_items": proposal["work_items"],
               "scopes": scopes, "unresolved": unresolved, "complete": True,
               "ready_for_drafting": not unresolved}
    return {**payload, "digest": digest(payload)}


def validate_repository_plan(plan: dict, context: dict) -> None:
    expected = finalize_repository_plan(context, plan["proposal"])
    if plan != expected:
        raise RepositoryError("Repository plan content or binding changed")
    if not plan["ready_for_drafting"]:
        raise RepositoryError("Repository plan has unresolved required work")


def validate_epic_scopes(plan: dict, epics: list[dict]) -> None:
    if not plan.get("ready_for_drafting"):
        raise RepositoryError("Repository plan is unresolved")
    scopes = {scope["scope_id"]: scope for scope in plan["scopes"]}
    actual = {}
    for epic in epics:
        scope_id = epic.get("repository_scope")
        if scope_id not in scopes or scope_id in actual:
            raise RepositoryError("Missing or duplicate repository epic scope")
        scope = scopes[scope_id]
        if (sorted(epic.get("work_item_ids", [])) != sorted(scope["work_item_ids"])
                or epic.get("mapping_snapshot_digest") != plan["mapping_snapshot_digest"]):
            raise RepositoryError("Epic work/snapshot conflicts with repository plan")
        expected_type = "Implementation" if scope["kind"] == "implementation" else "Investigation"
        if epic.get("type") != expected_type or epic.get("target_repository") != scope.get("target_repository"):
            raise RepositoryError("Epic type/target conflicts with repository scope")
        if epic.get("components") != scope["components"] or epic.get("component") != scope["components"][0]:
            raise RepositoryError("Epic components conflict with repository scope")
        if epic.get("implementation_type") != scope["implementation_type"]:
            raise RepositoryError("Epic delivery type conflicts with repository scope")
        if epic.get("priority") != scope["priority"] or epic.get("branch") != scope.get("branch"):
            raise RepositoryError("Epic priority/branch conflicts with repository scope")
        actual[scope_id] = epic
    if actual.keys() != scopes.keys():
        raise RepositoryError("Decomposition omits repository scopes")
    for scope_id, epic in actual.items():
        scope = scopes[scope_id]
        expected = sorted(actual[dep]["epic_id"] for dep in scope["dependencies"])
        if sorted(epic.get("dependencies", [])) != expected:
            raise RepositoryError("Epic dependencies conflict with repository scope plan")
        expected_gate = actual[scope["gated_by"]]["epic_id"] if scope.get("gated_by") else None
        if epic.get("gated_by") != expected_gate:
            raise RepositoryError("Epic Investigation gate conflicts with repository scope plan")


def decomposition_digest(plan: dict, artifacts_dir: Path, source_key: str) -> str:
    from artifact_utils import read_frontmatter
    files = []
    for path in sorted((Path(artifacts_dir) / "epic-tasks").glob(f"{source_key}-*.md")):
        metadata, body = read_frontmatter(path)
        metadata = {k: v for k, v in metadata.items() if k not in ("jira_key", "revised", "decompose_complete")}
        files.append({"name": path.name, "metadata": metadata, "body": body})
    if not files:
        raise RepositoryError("Cannot review missing decomposition artifacts")
    return digest({"repository_plan_digest": plan["digest"], "artifacts": files})


def paths(artifacts_dir, key):
    root = Path(artifacts_dir) / "repository-plans"
    return {"context": root / f"{key}-context.json", "proposal": root / f"{key}-proposal.json",
            "plan": root / f"{key}.json"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "finalize", "validate"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--artifacts-dir", default="artifacts", type=Path)
    args = parser.parse_args()
    try:
        selected = load_config()
        selected.validate_identity(args.source)
        locations = paths(args.artifacts_dir, args.source)
        if args.command == "prepare":
            context = load_repository_context(selected, args.artifacts_dir / f"strat-tasks/{args.source}.md", args.artifacts_dir)
            atomic_json(locations["context"], context)
            if context["status"] != "ready":
                atomic_json(locations["plan"], finalize_repository_plan(context, {}))
                return 2
            return 0
        context = json.loads(locations["context"].read_text())
        if args.command == "finalize":
            plan = finalize_repository_plan(context, json.loads(locations["proposal"].read_text()))
            if context.get("status") == "ready":
                components = selected.canonical_components()
                for item in plan["work_items"]:
                    selected.validate_component(item["component"], components)
            atomic_json(locations["plan"], plan)
            return 0 if plan["ready_for_drafting"] else 2
        validate_repository_plan(json.loads(locations["plan"].read_text()), context)
        return 0
    except (RepositoryError, OSError, ValueError, KeyError) as exc:
        print(f"Repository planning failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
