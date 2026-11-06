"""Synthetic Git-backed RHAI fixtures, never production approval data."""
from dataclasses import replace
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))



def module(name):
    assert importlib.util.find_spec(name) is not None, f"Missing {name} implementation"
    return importlib.import_module(name)


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def inventory():
    return {"version": 1, "revision": "fixture-v1", "repositories": [
        {"inventory_key": key, "provider": "github", "host": "github.com",
         "namespace": "example", "repository": key,
         "label": f"target-repo-{key}", "approval_reference": "synthetic-fixture-only",
         "aliases": []}
        for key in ("operator", "dashboard", "guidelines")]}


def git_source(tmp_path):
    repo = tmp_path / "sources"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.org")
    (repo / "inventory.yaml").write_text(yaml.safe_dump(inventory()))
    (repo / "mapping.md").write_text("Manager and cloudmanager changes belong in operator.\n")
    (repo / "architecture.json").write_text(json.dumps({"operator": ["manager", "cloudmanager"]}))
    git(repo, "add", ".")
    git(repo, "commit", "--signoff", "-m", "Fixture input")
    return repo


def policy(repo):
    return {"mode": "one-implementation-epic-per-planned-pr",
            "inventory": {"source_id": "mapping", "path": "inventory.yaml"},
            "sources": [{"id": "mapping", "url": repo.as_uri(), "ref": "main",
                         "paths": ["inventory.yaml", "mapping.md", "architecture.json"],
                         "role": "authoritative", "owner": "fixture-maintainers"}]}


def configured(repo):
    base = module("rhai_config").load_config()
    return replace(base, repository_policy=policy(repo))


def prepared_architecture(root):
    """The Git checkout normally prepared by fetch-architecture-context.sh."""
    checkout = Path(root) / ".context/architecture-context"
    checkout.mkdir(parents=True)
    git(checkout, "init", "-b", "main")
    git(checkout, "config", "user.name", "Fixture")
    git(checkout, "config", "user.email", "fixture@example.org")
    git(checkout, "remote", "add", "origin", "https://github.com/opendatahub-io/architecture-context")
    for relative, content in {
        "architecture/rhoai-3.0/operator.md": "Manager and cloudmanager are operator modules.\n",
        "overlays/operator.md": "Operator architecture overlay.\n",
    }.items():
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(checkout, "add", ".")
    git(checkout, "commit", "--signoff", "-m", "Prepared architecture fixture")
    git(checkout, "sparse-checkout", "init", "--cone")
    git(checkout, "sparse-checkout", "set", "architecture/rhoai-3.0", "overlays")
    return checkout


@pytest.fixture
def architecture_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return prepared_architecture(tmp_path)


def strategy(tmp_path):
    path = tmp_path / "artifacts/strat-tasks/RHAISTRAT-9001.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Synthetic operator strategy\n\nHLR-1: Manager logs.\nHLR-2: Cloudmanager logs.\nAC-1: Manager log tests pass.\nAC-2: Cloudmanager log tests pass.\n")
    return path


def proposal(context):
    return {"schema_version": 1, "source_key": "RHAISTRAT-9001",
            "mapping_snapshot_digest": context["digest"],
            "requirements": [{"id": key, "kind": kind, "quote": quote}
                             for key, kind, quote in [
                                 ("HLR-1", "requirement", "HLR-1: Manager logs."),
                                 ("HLR-2", "requirement", "HLR-2: Cloudmanager logs."),
                                 ("AC-1", "acceptance", "AC-1: Manager log tests pass."),
                                 ("AC-2", "acceptance", "AC-2: Cloudmanager log tests pass.")]],
            "work_items": [
                {"id": f"W{n}", "kind": "implementation", "description": desc,
                 "planned_pr": "PR-operator",
                 "requirement_refs": [f"HLR-{n}"], "acceptance_refs": [f"AC-{n}"],
                 "acceptance_criteria": [{"source_ref": f"AC-{n}", "text": f"{desc} tests pass"}],
                 "priority": "P0", "component": component, "team": team,
                 "delivery_type": "general", "depends_on": [],
                 "resolution": {"status": "resolved", "target_key": "operator",
                                "evidence_refs": [{"source_id": "mapping", "path": "mapping.md",
                                    "quote": "Manager and cloudmanager changes belong in operator."}]}}
                for n, desc, component, team in [(1, "Manager logging", "Operator", "Team A"),
                                                (2, "Cloudmanager logging", "Cloudmanager", "Team B")]]}


def epic_metadata(plan):
    identities = {scope["scope_id"]:
                  f"{plan['source_key']}-" + (f"BRANCH-{scope['branch']}-" if scope.get("branch") else "") + f"E{n:03d}"
                  for n, scope in enumerate(plan["scopes"], 1)}
    result = []
    for scope in plan["scopes"]:
        data = {"epic_id": identities[scope["scope_id"]], "title": "Repository-scoped work",
                "parent_strat": plan["source_key"], "component": scope["components"][0],
                "components": scope["components"], "team": scope["teams"][0],
                "type": "Implementation" if scope["kind"] == "implementation" else "Investigation",
                "priority": scope["priority"], "repository_scope": scope["scope_id"],
                "work_item_ids": scope["work_item_ids"],
                "mapping_snapshot_digest": plan["mapping_snapshot_digest"],
                "dependencies": [identities[dep] for dep in scope["dependencies"]]}
        for key in ("target_repository", "repository_warning", "implementation_type", "branch"):
            if scope.get(key) is not None: data[key] = deepcopy(scope[key])
        if scope.get("gated_by"): data["gated_by"] = identities[scope["gated_by"]]
        result.append(data)
    return result


def write_artifact(path, data, schema, *, body):
    import artifact_utils as artifacts
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    artifacts.write_frontmatter(path, data, schema)


def write_repository_decomposition(source, plan):
    import artifact_utils as artifacts
    root = Path(source).parent.parent
    metadata = {k: v for k, v in artifacts.read_frontmatter(source)[0].items()
                if k in artifacts._REPOSITORY_SOURCE_FIELDS}
    for epic in epic_metadata(plan):
        members = [item for item in plan["work_items"] if item["id"] in epic["work_item_ids"]]
        body = "## Scope\n" + "\n".join(item["description"] for item in members)
        body += "\n## Acceptance Criteria\n" + "\n".join(ac["text"] for item in members for ac in item["acceptance_criteria"])
        write_artifact(root / f"epic-tasks/{epic['epic_id']}.md",
                                    {**metadata, **epic}, "epic-task", body=body + "\n")
    summary = {**metadata, "parent_strat": plan["source_key"], "epic_count": len(plan["scopes"]),
               "critical_path_length": 1, "decompose_complete": True,
               "mapping_snapshot_digest": plan["mapping_snapshot_digest"]}
    write_artifact(root / f"epic-tasks/{plan['source_key']}-decomposition.md", summary,
                                "decomp-summary", body="HLR-1, HLR-2, AC-1, AC-2 covered\n")
    return metadata
