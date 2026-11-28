#!/usr/bin/env python3
"""Load the declared sources of a repository-resolution configuration.

Usage:
    python3 sources.py validate --config config/resolver.yaml
    python3 sources.py load --config config/resolver.yaml --out <dir>

`validate` checks the configuration only; nothing is fetched.

`load` validates the configuration, then for each declared source resolves
`ref` to a commit SHA, snapshots the declared paths at that commit into
`<dir>/<source id>/`, records a content hash, and writes `<dir>/manifest.json`.

Loader contract (ADR-0176):
- a source URL is credential-free HTTPS or the literal `self` (the consuming
  repository, pinned to its HEAD); anything else is rejected;
- declared paths are relative, canonical, and stay inside the source root;
- refs are resolved to a commit SHA before any content is read;
- snapshots are read from git objects at that SHA, never from a working tree.

Every failure is a hard error before resolution starts. Errors are labelled
`config` (the declaration is wrong; retrying will not help) or `fetch` (the
source could not be retrieved; a later retry may succeed). The label is for
reporting only — both stop the run.
"""

import argparse
import hashlib
import json
import os
import posixpath
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

import yaml

MANIFEST_NAME = "manifest.json"
SELF = "self"
SELF_REF = "HEAD"
ROLE_LIMITS = {"architecture": (1, 1), "mapping": (0, 1), "inventory": (0, 1)}
_FULL_SHA_LEN = 40


class SourceError(Exception):
    """A source could not be validated or loaded. Always a hard failure."""

    kind = "error"


class SourceConfigError(SourceError):
    kind = "config"


class SourceFetchError(SourceError):
    kind = "fetch"


# --- configuration -----------------------------------------------------------

def read_config(config_path, schema_path=None):
    """Read and validate the configuration. Returns (config, raw_bytes)."""
    config_path = Path(config_path)
    try:
        raw = config_path.read_bytes()
    except OSError as e:
        raise SourceConfigError(f"cannot read {config_path}: {e.strerror}") from e
    try:
        config = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise SourceConfigError(f"{config_path} is not valid YAML: {e}") from e
    validate_config(config, schema_path or _default_schema(config_path))
    return config, raw


def _default_schema(config_path):
    # config/resolver.yaml -> config/_schema/resolver.schema.json
    return Path(config_path).parent / "_schema" / "resolver.schema.json"


def validate_config(config, schema_path):
    try:
        import jsonschema
    except ImportError as e:
        raise SourceConfigError(
            "jsonschema is required to validate the resolver configuration") from e
    try:
        schema = json.loads(Path(schema_path).read_text())
    except (OSError, ValueError) as e:
        raise SourceConfigError(f"cannot read schema {schema_path}: {e}") from e

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(config), key=lambda e: list(e.path))
    if errors:
        details = "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in errors)
        raise SourceConfigError(f"resolver configuration is invalid: {details}")

    # Rules a JSON Schema cannot express.
    ids = [s["id"] for s in config["sources"]]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise SourceConfigError(f"duplicate source ids: {', '.join(duplicates)}")

    for role, (low, high) in ROLE_LIMITS.items():
        count = sum(1 for s in config["sources"] if s["role"] == role)
        if not low <= count <= high:
            expected = str(low) if low == high else f"{low} to {high}"
            raise SourceConfigError(
                f"expected {expected} source(s) with role '{role}', found {count}")

    data_source = config["repository_data"]["source"]
    if data_source not in ids:
        raise SourceConfigError(
            f"repository_data.source '{data_source}' is not a declared source")

    for source in config["sources"]:
        check_url(source["url"])
        if source["url"] == SELF and source["ref"] != SELF_REF:
            raise SourceConfigError(
                f"source '{source['id']}': a 'self' source is pinned to "
                f"{SELF_REF}, got ref '{source['ref']}'")
        for path in source["paths"]:
            canonical_path(path)
    canonical_path(config["repository_data"]["path"])


def check_url(url):
    """Enforce the loader contract on a source URL. Returns it unchanged."""
    if url == SELF:
        return url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise SourceConfigError(f"source URL must be HTTPS or 'self': {url!r}")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise SourceConfigError("source URL must not carry credentials")
    if parts.port is not None or not parts.hostname:
        raise SourceConfigError(f"source URL must name a host without a port: {url!r}")
    if parts.query or parts.fragment:
        raise SourceConfigError(f"source URL must not have a query or fragment: {url!r}")
    if any(seg in ("", ".", "..") for seg in parts.path.split("/")[1:]):
        raise SourceConfigError(f"source URL path is not canonical: {url!r}")
    return url


def canonical_path(path):
    """Return a declared path in canonical form, or raise if it escapes the root."""
    if not path or path.startswith("/") or "\\" in path:
        raise SourceConfigError(f"declared path must be relative: {path!r}")
    if any(seg == ".." for seg in path.split("/")):
        raise SourceConfigError(f"declared path must not contain '..': {path!r}")
    normalized = posixpath.normpath(path)
    if normalized in (".", "") or normalized.startswith("../"):
        raise SourceConfigError(f"declared path escapes the source root: {path!r}")
    return normalized


# --- git ---------------------------------------------------------------------

def _git(args, cwd=None, input_bytes=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false")
    cmd = ["git", "-c", "credential.helper=", "-c", "core.askPass=/bin/false",
           "-c", "protocol.file.allow=never", *args]
    try:
        result = subprocess.run(cmd, cwd=cwd, env=env, input=input_bytes,
                                capture_output=True, check=False)
    except OSError as e:
        raise SourceFetchError(f"cannot run git: {e.strerror}") from e
    return result


def _git_ok(args, cwd=None, error=SourceFetchError, what="git command"):
    result = _git(args, cwd=cwd)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise error(f"{what} failed: {message[-1] if message else 'no output'}")
    return result.stdout.decode("utf-8", "replace").strip()


def fetch_remote(url, ref, paths, workdir):
    """Fetch `ref` of an HTTPS source into a fresh repository. Returns (git_dir, sha).

    Only the declared paths are materialised (sparse, partial clone), so large
    architecture repositories are not downloaded in full.
    """
    repo = Path(workdir)
    _git_ok(["init", "--quiet", str(repo)], what="git init")
    _git_ok(["remote", "add", "origin", url], cwd=repo, what="git remote add")
    _git_ok(["config", "remote.origin.promisor", "true"], cwd=repo, what="git config")
    _git_ok(["config", "remote.origin.partialclonefilter", "blob:none"], cwd=repo,
            what="git config")
    _git_ok(["fetch", "--quiet", "--depth", "1", "--filter=blob:none", "--no-tags",
             "origin", ref], cwd=repo, what=f"fetching {url} at {ref}")
    sha = _git_ok(["rev-parse", "--verify", "FETCH_HEAD^{commit}"], cwd=repo,
                  what="resolving fetched ref")
    _git_ok(["sparse-checkout", "set", "--no-cone", *[f"/{p}" for p in paths]],
            cwd=repo, what="git sparse-checkout")
    _git_ok(["checkout", "--quiet", "--detach", sha], cwd=repo,
            what=f"checking out {sha}")
    return repo, sha


def resolve_self(repo_root):
    """Resolve the consuming repository's HEAD. Returns (git_dir, sha)."""
    sha = _git_ok(["rev-parse", "--verify", f"{SELF_REF}^{{commit}}"], cwd=repo_root,
                  error=SourceConfigError, what="resolving this repository's HEAD")
    return Path(repo_root), sha


def snapshot(git_dir, sha, paths, dest):
    """Write the declared paths at `sha` into `dest` from git objects.

    Returns the sorted list of (relative path, file sha256). Symlinks and
    submodules are rejected: a snapshot holds regular files only.
    """
    listing = _git(["ls-tree", "-r", "-z", "--full-tree", sha, "--", *paths],
                   cwd=git_dir)
    if listing.returncode != 0:
        raise SourceFetchError("listing snapshot files failed")
    entries = [e for e in listing.stdout.decode("utf-8").split("\0") if e]

    found = {p: False for p in paths}
    files = []
    for entry in entries:
        meta, rel = entry.split("\t", 1)
        mode, kind, obj = meta.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise SourceConfigError(
                f"declared paths contain a non-regular file: {rel} ({mode})")
        rel_canonical = canonical_path(rel)
        for p in paths:
            if rel_canonical == p or rel_canonical.startswith(p.rstrip("/") + "/"):
                found[p] = True
        files.append((rel_canonical, obj))

    missing = sorted(p for p, hit in found.items() if not hit)
    if missing:
        raise SourceConfigError(
            f"declared paths not found at {sha}: {', '.join(missing)}")

    dest = Path(dest)
    digests = []
    for rel, obj in sorted(files):
        blob = _git(["cat-file", "blob", obj], cwd=git_dir)
        if blob.returncode != 0:
            raise SourceFetchError(f"reading {rel} at {sha} failed")
        target = dest / rel
        if not target.resolve().is_relative_to(dest.resolve()):
            raise SourceConfigError(f"snapshot path escapes the snapshot root: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.stdout)
        digests.append((rel, hashlib.sha256(blob.stdout).hexdigest()))
    return digests


def content_hash(digests):
    h = hashlib.sha256()
    for rel, digest in digests:
        h.update(f"{digest}  {rel}\n".encode("utf-8"))
    return h.hexdigest()


# --- load --------------------------------------------------------------------

def load(config_path, out_dir, repo_root=".", fetcher=fetch_remote, schema_path=None):
    """Validate the configuration and snapshot every declared source.

    Returns the manifest dict (also written to `<out_dir>/manifest.json`).
    """
    config, raw = read_config(config_path, schema_path)
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SourceConfigError(f"output directory is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = []
    for source in config["sources"]:
        paths = [canonical_path(p) for p in source["paths"]]
        with tempfile.TemporaryDirectory(prefix="resolver-source-") as tmp:
            if source["url"] == SELF:
                git_dir, sha = resolve_self(repo_root)
            else:
                git_dir, sha = fetcher(source["url"], source["ref"], paths,
                                       Path(tmp) / "repo")
            if len(sha) != _FULL_SHA_LEN:
                raise SourceFetchError(
                    f"source '{source['id']}' did not resolve to a full commit SHA")
            digests = snapshot(git_dir, sha, paths, out_dir / source["id"])
        sources.append({
            "id": source["id"],
            "role": source["role"],
            "url": source["url"],
            "ref": source["ref"],
            "sha": sha,
            "paths": paths,
            "snapshot": source["id"],
            "files": len(digests),
            "content_hash": content_hash(digests),
        })

    data = config["repository_data"]
    manifest = {
        "version": 1,
        "group": config["group"],
        "config_digest": hashlib.sha256(raw).hexdigest(),
        "repository_data": {**data, "path": canonical_path(data["path"])},
        "jira": config["jira"],
        "sources": sources,
    }
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def read_manifest(path):
    """Read a manifest and return (manifest, snapshot root directory)."""
    path = Path(path)
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise SourceConfigError(f"cannot read manifest {path}: {e}") from e
    return manifest, path.parent


def source_dir(manifest, root, source_id):
    for source in manifest["sources"]:
        if source["id"] == source_id:
            return Path(root) / source["snapshot"]
    raise SourceConfigError(f"source '{source_id}' is not in the manifest")


# --- CLI ---------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_validate = sub.add_parser("validate", help="validate the configuration only")
    p_validate.add_argument("--config", required=True)
    p_load = sub.add_parser("load", help="validate and snapshot every source")
    p_load.add_argument("--config", required=True)
    p_load.add_argument("--out", required=True)
    p_load.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            read_config(args.config)
            print(f"OK: {args.config} is valid")
        else:
            manifest = load(args.config, args.out, repo_root=args.repo_root)
            for s in manifest["sources"]:
                print(f"{s['id']}: {s['url']} {s['ref']} -> {s['sha']} "
                      f"({s['files']} files)", file=sys.stderr)
            print(Path(args.out) / MANIFEST_NAME)
    except SourceError as e:
        print(f"ERROR [{e.kind}]: {e}", file=sys.stderr)
        print("This is a configuration or source failure, not an unresolved "
              "result. Stop the run and report it.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
