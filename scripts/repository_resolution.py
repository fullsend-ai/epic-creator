#!/usr/bin/env python3
"""Pipeline glue for repository resolution (ADR-0176).

The resolution skill (skills/repository-resolution/) owns resolution. This
module owns what epic-creator does around it: the feature flag, loading the
declared sources once per run, preparing each strategy's request, and
verifying each result before decomposition uses it.

Usage:
    python3 scripts/repository_resolution.py flag
    python3 scripts/repository_resolution.py load
    python3 scripts/repository_resolution.py prepare <ID>
    python3 scripts/repository_resolution.py verify <ID> ...
    python3 scripts/repository_resolution.py check-epics <ID>
"""

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
ROOT = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = ROOT / "skills" / "repository-resolution" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

FLAG = "EPIC_CREATOR_REPOSITORY_MODE"
CONFIG_PATH = "config/resolver.yaml"
SOURCES_DIR = "tmp/repo-resolution/sources"
MANIFEST_PATH = f"{SOURCES_DIR}/manifest.json"
RESOLUTION_DIR = "artifacts/repo-resolution"


class FlagError(ValueError):
    """The feature flag has a value other than unset, empty, true, or false."""


def repository_mode(env=None):
    """Return True when repository resolution is on.

    Unset or empty means off (the production default). `true` and `false`
    are the only accepted values; anything else is a hard configuration
    error, so a malformed flag never silently selects legacy behavior.
    """
    value = (os.environ if env is None else env).get(FLAG, "")
    if value == "":
        return False
    if value in ("true", "false"):
        return value == "true"
    raise FlagError(f"{FLAG} must be 'true' or 'false' (or unset), got {value!r}")


def request_path(strat_id):
    return f"{RESOLUTION_DIR}/{strat_id}-request.json"


def result_path(strat_id):
    return f"{RESOLUTION_DIR}/{strat_id}-result.json"


def verified_path(strat_id):
    return f"{RESOLUTION_DIR}/{strat_id}-verified.json"


def load_sources(config=CONFIG_PATH, out=SOURCES_DIR):
    """Validate the resolver config and snapshot every declared source.

    Raises sources.SourceError on any configuration or fetch failure.
    """
    import sources
    if os.path.exists(out):
        shutil.rmtree(out)
    sources.load(config, out, repo_root=str(ROOT))
    return f"{out}/manifest.json"


def ensure_sources(config=CONFIG_PATH, out=SOURCES_DIR):
    """Reuse sources already loaded for this configuration, else load them.

    The fullsend pre-script loads sources on the host (the sandbox has no
    GitHub access). A manifest is reused only when it was produced from the
    current configuration file and every snapshot directory is present.
    """
    import hashlib
    from sources import SourceConfigError, read_manifest
    manifest_path = f"{out}/manifest.json"
    if os.path.exists(manifest_path):
        manifest, root = read_manifest(manifest_path)
        try:
            digest = hashlib.sha256(Path(config).read_bytes()).hexdigest()
        except OSError as e:
            raise SourceConfigError(f"cannot read {config}: {e.strerror}") from e
        if manifest.get("config_digest") == digest and all(
                (Path(root) / s["snapshot"]).is_dir() for s in manifest["sources"]):
            return manifest_path
    return load_sources(config, out)


def prepare_request(strat_id):
    """Write the resolver request (description + components) for a strategy."""
    from artifact_utils import read_frontmatter
    data, body = read_frontmatter(f"artifacts/strat-tasks/{strat_id}.md")
    if not data:
        raise ValueError(f"strategy {strat_id} has no frontmatter")
    request = {"description": body.strip(),
               "components": list(data.get("components") or [])}
    os.makedirs(RESOLUTION_DIR, exist_ok=True)
    for stale in (result_path(strat_id), verified_path(strat_id)):
        if os.path.exists(stale):
            os.remove(stale)
    with open(request_path(strat_id), "w") as f:
        json.dump(request, f, indent=2)
    return request_path(strat_id)


def verify_strategy(strat_id, manifest_path=None):
    """Run the skill's verification on one strategy's result.

    Writes `<ID>-verified.json` and returns it. A result that fails
    verification is moved aside (`<ID>-result.invalid.json`) so a retry
    starts clean. Configuration/source failures raise.
    """
    import verify as skill_verify
    from lookup import read_request
    from sources import read_manifest

    manifest, root = read_manifest(manifest_path or MANIFEST_PATH)
    request = read_request(request_path(strat_id))
    try:
        result = json.loads(Path(result_path(strat_id)).read_text())
        errors = skill_verify.verify(manifest, root, request, result)
    except (OSError, ValueError) as e:
        result, errors = None, [f"result: cannot read {result_path(strat_id)}: {e}"]

    record = {
        "strat_id": strat_id,
        "verified": not errors,
        "errors": errors,
        "result_status": result.get("status") if isinstance(result, dict) else None,
        "sources": [{"id": s["id"], "url": s["url"], "sha": s["sha"]}
                    for s in manifest["sources"]],
    }
    if errors and os.path.exists(result_path(strat_id)):
        os.replace(result_path(strat_id),
                   f"{RESOLUTION_DIR}/{strat_id}-result.invalid.json")
    with open(verified_path(strat_id), "w") as f:
        json.dump(record, f, indent=2)
    return record


def epic_files(strat_id, epic_dir="artifacts/epic-tasks"):
    """Main-plan and branch epic files of a strategy (no summary/signal files)."""
    import glob
    paths = glob.glob(f"{epic_dir}/{strat_id}-E[0-9][0-9][0-9].md") + \
        glob.glob(f"{epic_dir}/{strat_id}-BRANCH-*-E[0-9][0-9][0-9].md")
    return sorted(paths)


def check_epics(strat_id, manifest_path=None):
    """Check every epic's repository assignment against verified results.

    Returns a list of errors (empty when every epic is consistent):
    - every epic names a result file under artifacts/repo-resolution/;
    - that result (with its request) passes the skill's verification;
    - a resolved epic's repository_key is one of that result's repositories;
    - an unresolved epic (repository_key null) points at an unresolved result.
    Configuration/source failures raise.
    """
    import verify as skill_verify
    from artifact_utils import read_frontmatter
    from lookup import read_request
    from sources import read_manifest

    manifest, root = read_manifest(manifest_path or MANIFEST_PATH)
    checked = {}
    errors = []
    files = epic_files(strat_id)
    if not files:
        return [f"{strat_id}: no epic files found"]
    for path in files:
        name = os.path.basename(path)
        data, _ = read_frontmatter(path)
        data = data or {}
        key = data.get("repository_key")
        res_path = data.get("repository_resolution")
        if not res_path:
            errors.append(f"{name}: repository_resolution is not set")
            continue
        if not res_path.startswith(f"{RESOLUTION_DIR}/") or \
                not res_path.endswith("-result.json"):
            errors.append(f"{name}: repository_resolution {res_path!r} is not a "
                          "result file")
            continue
        if res_path not in checked:
            req_path = res_path[:-len("-result.json")] + "-request.json"
            try:
                result = json.loads(Path(res_path).read_text())
                request = read_request(req_path)
                result_errors = skill_verify.verify(manifest, root, request, result)
            except (OSError, ValueError) as e:
                result, result_errors = None, [f"cannot read {res_path}: {e}"]
            checked[res_path] = (result, result_errors)
        result, result_errors = checked[res_path]
        if result_errors:
            errors.extend(f"{name}: {res_path}: {e}" for e in result_errors)
            continue
        if result["status"] == "resolved":
            keys = [r["key"] for r in result["repositories"]]
            if key not in keys:
                errors.append(f"{name}: repository_key {key!r} is not in {res_path} "
                              f"(resolved: {keys})")
        elif key is not None:
            errors.append(f"{name}: repository_key {key!r} set, but {res_path} is "
                          "unresolved")
    return errors


COMMENT_MARKER = "Repository resolution (epic-creator): unresolved"


def _source_line(manifest, evidence):
    source = next((s for s in manifest["sources"] if s["id"] == evidence["source"]),
                  None)
    if source is None:
        return f"`{evidence['path']}`"
    return f"`{evidence['path']}` in {source['url']} at `{source['sha']}`"


def _evidence_md(manifest, evidence_list):
    lines = []
    for ev in evidence_list:
        lines.append(f"- {_source_line(manifest, ev)}")
        quote = " ".join(ev["quote"].split())
        lines.append(f"  > {quote}")
    return "\n".join(lines) if lines else "- (none)"


def publication(frontmatter, manifest_path=None):
    """What to publish for one epic in repository mode.

    Built only from the verified result file the epic names — never from
    model-written epic text. Returns a dict:
      status: "resolved" | "unresolved"
      url: canonical repository URL (resolved only)
      field_id: the configured Jira URL field
      evidence_md: markdown evidence attachment (resolved only)
      comment_md: warning comment (unresolved only)
    Raises ValueError when the epic has no usable result; callers run
    check_epics first, so this should not happen on a verified run.
    """
    from lookup import resolve_details
    from sources import read_manifest

    manifest, root = read_manifest(manifest_path or MANIFEST_PATH)
    res_path = frontmatter.get("repository_resolution")
    if not res_path:
        raise ValueError(f"{frontmatter.get('epic_id')}: no repository_resolution")
    result = json.loads(Path(res_path).read_text())
    field_id = manifest["jira"]["repository_url_field"]
    epic_id = frontmatter.get("epic_id")

    if result["status"] == "resolved":
        key = frontmatter.get("repository_key")
        repo = next((r for r in result["repositories"] if r["key"] == key), None)
        if repo is None:
            raise ValueError(f"{epic_id}: repository {key!r} is not in {res_path}")
        md = "\n".join([
            f"# Repository resolution — {epic_id}",
            "",
            f"Repository: {repo['url']} (key `{repo['key']}`)",
            f"Phase 1 hint: {repo['hint']}",
            "",
            "## Evidence (cited verbatim from the pinned architecture context)",
            "",
            _evidence_md(manifest, repo["evidence"]),
            "",
        ])
        return {"status": "resolved", "url": repo["url"], "field_id": field_id,
                "evidence_md": md, "comment_md": None}

    candidates = result.get("candidates") or []
    try:
        details = resolve_details(manifest, root, candidates) if candidates else []
        candidate_lines = [f"- {d['url']} (key `{d['key']}`)" for d in details]
    except Exception:
        candidate_lines = [f"- `{c}`" for c in candidates]
    md = "\n".join([
        f"**{COMMENT_MARKER}**",
        "",
        "No repository could be assigned to this epic with evidence, so the "
        "repository field is empty. Please set it manually, or fix the inputs "
        "and re-run resolution.",
        "",
        f"**Reason:** {result['reason']}",
        "",
        "**Candidates:**",
        "\n".join(candidate_lines) if candidate_lines else "- (none)",
        "",
        "**Available evidence:**",
        _evidence_md(manifest, result.get("evidence") or []),
    ])
    return {"status": "unresolved", "url": None, "field_id": field_id,
            "evidence_md": None, "comment_md": md}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__, file=sys.stderr)
        return 1
    command, args = argv[0], argv[1:]
    from sources import SourceError
    from lookup import ResolutionError
    try:
        if command == "flag":
            print("on" if repository_mode() else "off")
        elif command == "load":
            print(load_sources())
        elif command == "prepare" and len(args) == 1:
            print(prepare_request(args[0]))
        elif command == "verify" and args:
            failed = 0
            for strat_id in args:
                record = verify_strategy(strat_id)
                state = "verified" if record["verified"] else "INVALID"
                print(f"{strat_id}: {state} ({record['result_status']})")
                for error in record["errors"]:
                    print(f"  - {error}", file=sys.stderr)
                failed += not record["verified"]
            return 1 if failed else 0
        elif command == "check-epics" and len(args) == 1:
            errors = check_epics(args[0])
            for error in errors:
                print(f"  - {error}")
            print(f"{args[0]}: {'OK' if not errors else f'{len(errors)} problem(s)'}")
            return 1 if errors else 0
        else:
            print(__doc__, file=sys.stderr)
            return 1
    except (FlagError, SourceError, ResolutionError, ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
