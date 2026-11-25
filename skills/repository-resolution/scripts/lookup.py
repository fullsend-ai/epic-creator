#!/usr/bin/env python3
"""Deterministic lookups for repository resolution (no model judgment).

Usage:
    python3 lookup.py hints   --manifest <manifest.json> --request <request.json>
    python3 lookup.py details --manifest <manifest.json> --keys <key1>,<key2>

`hints` is Phase 1: Jira components -> repository keys, from the group's
mapping source. It prints `{"skipped": "no-components"}` or
`{"skipped": "no-mapping"}` when Phase 1 does not apply.

`details` resolves repository keys to namespace path, name, and the canonical
URL, from the architecture context's structured repository data. A key that
cannot be resolved is a hard failure.

Canonical URL (ADR-0176): `https://<host>/<namespace path>/<name>`, all
lowercase, no `.git`, no trailing slash. The namespace path may be nested
(`group/subgroup`). A URL that cannot be normalized is a hard error.
"""

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import SourceError, read_manifest, source_dir  # noqa: E402

_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_SSH = re.compile(r"^git@(?P<host>[^:/]+):(?P<path>[^:]+)$")


class ResolutionError(Exception):
    """A deterministic lookup failed. Always a hard failure."""

    kind = "resolution"


# --- canonical URL -----------------------------------------------------------

def canonical_repo_url(host, namespace, name):
    """Build the canonical URL from its parts, or raise if a part is invalid."""
    host = (host or "").lower()
    if not _HOST.match(host):
        raise ResolutionError(f"invalid repository host: {host!r}")
    segments = (namespace or "").lower().split("/")
    name = (name or "").lower()
    if name.endswith(".git"):
        name = name[:-4]
    for segment in [*segments, name]:
        if segment in (".", "..") or not _SEGMENT.match(segment):
            raise ResolutionError(
                f"invalid repository path segment {segment!r} in "
                f"{namespace!r}/{name!r}")
    return f"https://{host}/{'/'.join(segments)}/{name}"


def normalize_repo_url(url):
    """Normalize an HTTPS or SSH repository URL to the canonical form.

    Raises ResolutionError for anything that cannot be normalized safely:
    other schemes, userinfo, ports, query strings, fragments, `.`/`..` or
    empty path segments, or a path without both a namespace and a name.
    """
    if not isinstance(url, str) or not url:
        raise ResolutionError("repository URL is empty")
    ssh = _SSH.match(url)
    if ssh:
        host, path = ssh.group("host"), ssh.group("path")
    else:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme.lower() != "https":
            raise ResolutionError(f"repository URL must be HTTPS or SSH: {url!r}")
        if "@" in parts.netloc:
            raise ResolutionError("repository URL must not carry userinfo")
        if parts.port is not None:
            raise ResolutionError(f"repository URL must not carry a port: {url!r}")
        if parts.query or parts.fragment or url.endswith(("?", "#")):
            raise ResolutionError(
                f"repository URL must not have a query or fragment: {url!r}")
        host, path = parts.hostname or "", parts.path
    path = path.lstrip("/")
    if path.endswith("/"):
        path = path[:-1]  # one trailing slash is tolerated
    segments = path.split("/")
    if len(segments) < 2 or any(s in ("", ".", "..") for s in segments):
        raise ResolutionError(f"repository URL path cannot be normalized: {url!r}")
    return canonical_repo_url(host, "/".join(segments[:-1]), segments[-1])


# --- data loading ------------------------------------------------------------

def _load_json(path, what):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise ResolutionError(f"cannot read {what} {path}: {e}") from e


def load_component_map(manifest, root):
    """Return {repository key: entry} from the structured repository data."""
    data = manifest["repository_data"]
    path = source_dir(manifest, root, data["source"]) / data["path"]
    raw = _load_json(path, "repository data")
    components = raw.get("components") if isinstance(raw, dict) else None
    if not isinstance(components, dict) or not components:
        raise ResolutionError(f"repository data has no components: {data['path']}")
    return components


def resolve_details(manifest, root, keys, components=None):
    """Resolve repository keys to {key, namespace, name, url}. Unknown key = error."""
    components = components if components is not None else \
        load_component_map(manifest, root)
    host = manifest["repository_data"]["host"]
    details = []
    for key in keys:
        entry = components.get(key)
        if not isinstance(entry, dict):
            raise ResolutionError(
                f"repository key {key!r} is not in the repository data")
        namespace, name = entry.get("repo_org"), entry.get("repo_name")
        if not namespace or not name:
            raise ResolutionError(
                f"repository key {key!r} has no namespace path or name")
        url = canonical_repo_url(host, namespace, name)
        details.append({"key": key, "namespace": namespace.lower(),
                        "name": name.lower(), "url": url})
    return details


def _mapping_source(manifest):
    for source in manifest["sources"]:
        if source["role"] == "mapping":
            return source
    return None


def load_mapping(manifest, root, components_map=None):
    """Return {Jira component: [repository keys]}, or None when no mapping.

    Every key in the mapping must exist in the repository data: a stale key
    is a hard configuration failure, not a silent miss.
    """
    source = _mapping_source(manifest)
    if source is None:
        return None
    if len(source["paths"]) != 1:
        raise ResolutionError("the mapping source must declare exactly one file")
    path = Path(root) / source["snapshot"] / source["paths"][0]
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise ResolutionError(f"cannot read mapping {source['paths'][0]}: {e}") from e

    mapping = raw.get("components") if isinstance(raw, dict) else None
    if not isinstance(mapping, dict):
        raise ResolutionError("mapping must have a 'components' table")
    for component, keys in mapping.items():
        if not isinstance(component, str) or not isinstance(keys, list) or \
                not keys or not all(isinstance(k, str) and k for k in keys):
            raise ResolutionError(
                f"mapping entry {component!r} must list one or more repository keys")

    known = components_map if components_map is not None else \
        load_component_map(manifest, root)
    stale = sorted({k for keys in mapping.values() for k in keys if k not in known})
    if stale:
        raise ResolutionError(
            f"mapping lists keys missing from the repository data: {', '.join(stale)}")
    return mapping


def phase1_hints(manifest, root, components):
    """Phase 1: the union of mapped repository keys for the given components."""
    components = [c for c in (components or []) if isinstance(c, str) and c]
    if not components:
        return {"skipped": "no-components"}
    mapping = load_mapping(manifest, root)
    if mapping is None:
        return {"skipped": "no-mapping"}
    hints = sorted({key for c in components for key in mapping.get(c, [])})
    unmapped = sorted({c for c in components if c not in mapping})
    return {"hints": hints, "unmapped_components": unmapped}


def read_request(path):
    request = _load_json(path, "request")
    if not isinstance(request, dict) or not isinstance(request.get("description"), str):
        raise ResolutionError("request must be an object with a 'description' string")
    components = request.get("components", [])
    if not isinstance(components, list):
        raise ResolutionError("request 'components' must be a list")
    return request


# --- CLI ---------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_hints = sub.add_parser("hints", help="Phase 1: components -> repository keys")
    p_hints.add_argument("--manifest", required=True)
    p_hints.add_argument("--request", required=True)
    p_details = sub.add_parser("details", help="repository keys -> full details")
    p_details.add_argument("--manifest", required=True)
    p_details.add_argument("--keys", required=True)
    args = parser.parse_args(argv)

    try:
        manifest, root = read_manifest(args.manifest)
        if args.command == "hints":
            request = read_request(args.request)
            result = phase1_hints(manifest, root, request.get("components"))
        else:
            keys = [k.strip() for k in args.keys.split(",") if k.strip()]
            if not keys:
                raise ResolutionError("no repository keys given")
            result = resolve_details(manifest, root, keys)
    except (ResolutionError, SourceError) as e:
        print(f"ERROR [{e.kind}]: {e}", file=sys.stderr)
        print("This is a hard failure, not an unresolved result. Stop and report it.",
              file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
