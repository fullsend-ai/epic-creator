"""Per-source terminal outcomes used by the batch pipeline."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


STATUS_DIR = Path("tmp/pipeline-status")
_VALID_STATUSES = {"invalid", "skipped"}


def _path(key):
    if not isinstance(key, str) or not key or "\0" in key:
        raise ValueError(f"Invalid Jira issue key for pipeline status: {key!r}")
    name = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return STATUS_DIR / f"{name}.json"


def _validate_record(key, status):
    if (not isinstance(status, dict)
            or status.get("key") != key
            or status.get("status") not in _VALID_STATUSES
            or not isinstance(status.get("phase"), str)
            or not isinstance(status.get("message"), str)):
        raise ValueError(f"Invalid pipeline status record for {key}")
    return status


def read_status(key):
    """Read one source's terminal status, if it has one."""
    path = _path(key)
    if not path.exists():
        return None
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read pipeline status for {key}: {exc}") from exc
    return _validate_record(key, status)


def all_statuses():
    """Return all per-source status records for the current pipeline run."""
    if not STATUS_DIR.exists():
        return {}
    statuses = {}
    for path in sorted(STATUS_DIR.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read pipeline status {path}: {exc}") from exc
        key = record.get("key") if isinstance(record, dict) else None
        status = _validate_record(key, record)
        if _path(key) != path:
            raise ValueError(f"Pipeline status filename conflicts with source key: {path}")
        statuses[key] = status
    return statuses


def write_status(key, status, phase, message):
    """Atomically record a source error or an intentional skip."""
    if status not in _VALID_STATUSES:
        raise ValueError(f"Unsupported pipeline status: {status!r}")
    path = _path(key)
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"key": key, "status": status, "phase": str(phase),
               "message": str(message)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=STATUS_DIR,
                prefix=f".{path.stem}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def clear_status(key):
    """Remove a source's status after a retry or successful recovery."""
    _path(key).unlink(missing_ok=True)


def clear_all_statuses():
    """Start a fresh pipeline run without carrying over prior outcomes."""
    shutil.rmtree(STATUS_DIR, ignore_errors=True)
