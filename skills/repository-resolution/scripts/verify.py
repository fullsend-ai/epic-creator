#!/usr/bin/env python3
"""Verify a repository resolution result before it is accepted (ADR-0176).

Usage:
    python3 verify.py --manifest <manifest.json> --request <request.json> --result <result.json>

Checks, all deterministic:
- schema: the result matches the output contract (resolved or unresolved);
- Phase 1: the recorded hints match a fresh Phase 1 lookup for the request;
  confirmed and overridden hints, and unresolved candidates, come from them;
- details: every repository's namespace, name, and URL equal the values the
  repository data gives for its key (the model never builds URLs);
- citations: every quote exists verbatim in a file of an architecture
  source's pinned snapshot, inside that source's declared paths;
- inventory: only when the group declares one, every repository is in it.

Exit codes:
    0  the result is valid
    1  the result is invalid — the resolver may fix its result and retry
    2  configuration or source failure — stop and report; not a result problem
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lookup import (ResolutionError, load_component_map, phase1_hints,  # noqa: E402
                    read_request, resolve_details)
from sources import (SourceConfigError, SourceError, canonical_path,  # noqa: E402
                     read_manifest)

RESULT_SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "result.schema.json"


def _schema_errors(result):
    import jsonschema
    schema = json.loads(RESULT_SCHEMA.read_text())
    validator = jsonschema.Draft202012Validator(schema)
    if validator.is_valid(result):
        return []
    # oneOf hides the useful message; validate against the branch the status names.
    branch = {"resolved": "resolved", "unresolved": "unresolved"}.get(
        result.get("status") if isinstance(result, dict) else None)
    if branch:
        sub = dict(schema["$defs"][branch], **{"$defs": schema["$defs"]})
        errors = jsonschema.Draft202012Validator(sub).iter_errors(result)
    else:
        errors = validator.iter_errors(result)
    return [f"schema: {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in sorted(errors, key=lambda e: list(e.path))] or \
        ["schema: result does not match the output contract"]


def _check_citation(manifest, root, evidence, where):
    """Return an error string, or None when the quote exists verbatim."""
    sources = {s["id"]: s for s in manifest["sources"]}
    source = sources.get(evidence["source"])
    if source is None or source["role"] != "architecture":
        return (f"{where}: source {evidence['source']!r} is not an architecture "
                "source; only the architecture context can justify an assignment")
    try:
        rel = canonical_path(evidence["path"])
    except SourceConfigError:
        return f"{where}: path {evidence['path']!r} is not a canonical relative path"
    if not any(rel == p or rel.startswith(p + "/") for p in source["paths"]):
        return f"{where}: path {rel!r} is outside the declared paths of {source['id']!r}"
    snapshot = (Path(root) / source["snapshot"]).resolve()
    target = (snapshot / rel).resolve()
    if not target.is_relative_to(snapshot) or not target.is_file():
        return f"{where}: file {rel!r} is not in the pinned snapshot"
    text = target.read_bytes().decode("utf-8", errors="replace")
    if evidence["quote"] not in text:
        return (f"{where}: quote not found verbatim in {rel} at "
                f"{source['sha'][:12]}")
    return None


def _inventory(manifest, root):
    """Return the set of approved repository keys, or None when none is declared."""
    source = next((s for s in manifest["sources"] if s["role"] == "inventory"), None)
    if source is None:
        return None
    if len(source["paths"]) != 1:
        raise ResolutionError("the inventory source must declare exactly one file")
    path = Path(root) / source["snapshot"] / source["paths"][0]
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise ResolutionError(f"cannot read inventory: {e}") from e
    keys = raw.get("repositories") if isinstance(raw, dict) else None
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        raise ResolutionError("inventory must have a 'repositories' list of keys")
    return set(keys)


def verify(manifest, root, request, result):
    """Return a list of result errors (empty when valid).

    Raises ResolutionError or SourceError for configuration/source failures.
    """
    errors = _schema_errors(result)
    if errors:
        return errors

    expected_phase1 = phase1_hints(manifest, root, request.get("components"))
    expected_phase1.pop("unmapped_components", None)
    if result["phase1"] != expected_phase1:
        errors.append(f"phase1: recorded {result['phase1']} but the lookup gives "
                      f"{expected_phase1}")
    hints = set(expected_phase1.get("hints", []))

    if result["status"] == "unresolved":
        extra = sorted(set(result["candidates"]) - hints)
        if extra:
            errors.append(f"candidates: {extra} are not Phase 1 hints")
        for i, ev in enumerate(result["evidence"]):
            error = _check_citation(manifest, root, ev, f"evidence[{i}]")
            if error:
                errors.append(error)
        return errors

    repos = result["repositories"]
    keys = [r["key"] for r in repos]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        errors.append(f"repositories: duplicate keys {duplicates}")

    components = load_component_map(manifest, root)
    for i, repo in enumerate(repos):
        where = f"repositories[{i}] ({repo['key']})"
        try:
            [expected] = resolve_details(manifest, root, [repo["key"]], components)
        except ResolutionError as e:
            errors.append(f"{where}: {e}")
            continue
        for field in ("namespace", "name", "url"):
            if repo[field] != expected[field]:
                errors.append(f"{where}: {field} is {repo[field]!r}, repository data "
                              f"gives {expected[field]!r}")
        if repo["hint"] == "confirmed" and repo["key"] not in hints:
            errors.append(f"{where}: marked confirmed but it is not a Phase 1 hint")
        if repo["hint"] == "none" and repo["key"] in hints:
            errors.append(f"{where}: is a Phase 1 hint; mark it confirmed")
        for j, ev in enumerate(repo["evidence"]):
            error = _check_citation(manifest, root, ev, f"{where} evidence[{j}]")
            if error:
                errors.append(error)

    for i, override in enumerate(result["overridden_hints"]):
        if override["key"] not in hints:
            errors.append(f"overridden_hints[{i}]: {override['key']!r} is not a "
                          "Phase 1 hint")
        if override["key"] in keys:
            errors.append(f"overridden_hints[{i}]: {override['key']!r} is also "
                          "returned as a repository")
    unhandled = sorted(hints - set(keys) - {o["key"] for o in result["overridden_hints"]})
    if unhandled:
        errors.append(f"phase1: hints {unhandled} are neither confirmed nor overridden")

    inventory = _inventory(manifest, root)
    if inventory is not None:
        outside = sorted(set(keys) - inventory)
        if outside:
            errors.append(f"inventory: {outside} are not in the group's inventory")
    return errors


def _load_result(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        return e


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)

    try:
        manifest, root = read_manifest(args.manifest)
        request = read_request(args.request)
        result = _load_result(args.result)
        if isinstance(result, Exception):
            errors = [f"result: cannot read {args.result}: {result}"]
        else:
            errors = verify(manifest, root, request, result)
    except (ResolutionError, SourceError) as e:
        print(f"ERROR [{e.kind}]: {e}", file=sys.stderr)
        print("This is a configuration or source failure, not a result problem. "
              "Stop and report it.", file=sys.stderr)
        return 2

    if errors:
        print("INVALID: the result does not pass verification:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"OK: {result['status']} result verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
