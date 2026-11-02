"""Fetch Git evidence sources configured for RHAI."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import unquote, urlsplit, urlunsplit

from rhai_config import RepositoryError


def _path(value):
    if not isinstance(value, str) or not value or value.startswith(("/", ":", "-")):
        raise RepositoryError("Invalid repository source path")
    path = PurePosixPath(value)
    if ".." in path.parts or "\\" in value:
        raise RepositoryError("Invalid repository source path")
    return str(path)


def guidance_reference(policy):
    reference = policy.get("guidance")
    if reference is None:
        return None
    if (not isinstance(reference, dict) or set(reference) != {"source_id", "path"}
            or reference["source_id"] not in {source["id"] for source in policy["sources"]}):
        raise RepositoryError("Invalid repository guidance source reference")
    path = _path(reference["path"])
    if Path(path).suffix.lower() not in (".md", ".markdown"):
        raise RepositoryError("Repository guidance must reference a Markdown file")
    return {"source_id": reference["source_id"], "path": path}


def normalize_git_source(reference: dict) -> dict:
    data = dict(reference)
    if not re.fullmatch(r"[a-z][a-z0-9-]*", str(data.get("id", ""))):
        raise RepositoryError("Invalid repository source ID")
    if data.get("url") == "self":
        data["url"] = Path(__file__).resolve().parents[1].as_uri()
    parsed = urlsplit(data.get("url", ""))
    if (parsed.scheme not in ("https", "file") or parsed.username or parsed.password
            or parsed.query or (parsed.scheme == "https" and not parsed.hostname)
            or (parsed.scheme == "file" and parsed.netloc)):
        raise RepositoryError("Source requires a credential-free HTTPS Git URL or local file URL")
    path = unquote(parsed.path).rstrip("/")
    marker = next((m for m in ("/-/blob/", "/-/tree/", "/blob/", "/tree/") if m in path), None)
    ref = data.get("ref")
    if marker:
        if not ref:
            raise RepositoryError("Git document links require an explicit ref")
        path, suffix = path.split(marker, 1)
        if not suffix.startswith(ref + "/"):
            raise RepositoryError("Document link does not match its configured ref")
        linked_path = _path(suffix[len(ref) + 1:])
        if data.get("paths") and data["paths"] != [linked_path]:
            raise RepositoryError("Document link conflicts with configured paths")
        data["paths"] = [linked_path]
    if not isinstance(ref, str) or not ref or ref.startswith("-") or any(c.isspace() for c in ref):
        raise RepositoryError("Repository source requires an explicit Git ref")
    paths = data.get("paths", ["."])
    if not isinstance(paths, list) or not paths:
        raise RepositoryError("Source paths must be a nonempty list")
    data["paths"] = list(dict.fromkeys(_path(p) for p in paths))
    if parsed.scheme == "https" and not path.endswith(".git"):
        path += ".git"
    data["url"] = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return data


def _git(directory, *args, binary=False):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    try:
        result = subprocess.run(["git", "-C", str(directory), "-c", "protocol.ext.allow=never",
                                 *args], env=env, capture_output=True, timeout=90, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        # Git diagnostics can contain credential-helper output or embedded URLs.
        raise RepositoryError("Unable to retrieve configured Git source/ref/path") from None
    return result.stdout if binary else result.stdout.decode("utf-8").strip()


def _snapshot(source, repository, cache, commit):
    files = {}
    for selected in source["paths"]:
        listing = _git(repository, "ls-tree", "-r", "--name-only", commit, "--", selected)
        if not listing:
            raise RepositoryError("Configured Git source path is missing")
        for name in listing.splitlines():
            name = _path(name)
            if Path(name).suffix.lower() not in (".md", ".markdown", ".yaml", ".yml", ".json", ".txt"):
                continue
            content = _git(repository, "show", f"{commit}:{name}", binary=True)
            dest = cache / "content" / commit / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            files[name] = {"sha256": hashlib.sha256(content).hexdigest(), "local_path": str(dest.resolve())}
    if not files:
        raise RepositoryError("Configured Git source has no supported evidence files")
    return {**source, "commit": commit, "files": files,
            "fetched_at": datetime.now(timezone.utc).isoformat()}


def snapshot_architecture_context(cache_root: Path) -> dict:
    """Pin the release and overlays already fetched by Epic Creator bootstrap."""
    checkout = Path(".context/architecture-context")
    paths = [path for path in _git(checkout, "sparse-checkout", "list").splitlines()
             if path.startswith("architecture/") or path == "overlays" or path.startswith("overlays/")]
    if not any(path.startswith("architecture/") for path in paths):
        raise RepositoryError("Run scripts/fetch-architecture-context.sh before repository planning")
    source = normalize_git_source({
        "id": "architecture-context", "url": _git(checkout, "remote", "get-url", "origin"),
        "ref": "HEAD", "paths": paths, "role": "supporting",
        "owner": "architecture-context maintainers"})
    commit = _git(checkout, "rev-parse", "HEAD^{commit}")
    return _snapshot(source, checkout, Path(cache_root) / source["id"], commit)


def fetch_git_sources(policy: dict, cache_root: Path) -> list[dict]:
    manifests = []
    for reference in policy["sources"]:
        source = normalize_git_source(reference)
        cache = Path(cache_root) / source["id"]
        cache.mkdir(parents=True, exist_ok=True)
        if not (cache / "HEAD").exists():
            _git(cache, "init", "--bare")
        _git(cache, "fetch", "--depth=1", "--no-tags", "--", source["url"], source["ref"])
        commit = _git(cache, "rev-parse", "FETCH_HEAD^{commit}")
        manifests.append(_snapshot(source, cache, cache, commit))
    return manifests
