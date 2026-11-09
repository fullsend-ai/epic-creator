"""Durable submission ledger and per-run locking.

The ledger records the before/after state of every protected Jira mutation so
interrupted submissions can be audited and resumed.  The lock prevents two
processes from submitting the same run concurrently.
"""

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

LEDGER_SCHEMA_VERSION = 1


class SubmissionLedgerError(RuntimeError):
    """A submission ledger is missing required or invalid data."""


class SubmissionLockError(RuntimeError):
    """Another process already owns the submission lock."""


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f"{os.path.basename(path)}.", suffix=".tmp",
        dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(descriptor, "w") as target:
            json.dump(data, target, indent=2, sort_keys=True)
            target.write("\n")
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def ledger_path(artifacts_dir, run_id):
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)
    return os.path.join(artifacts_dir, "submission-ledgers",
                        f"{safe_run_id}.json")


class SubmissionLedger:
    """Persist before/after state for each Jira mutation in one run."""

    def __init__(self, path, run_id, manifest_digest):
        self.path = path
        self.run_id = run_id
        self.manifest_digest = manifest_digest
        self.entries = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            self._save()
            return
        with open(self.path) as source:
            try:
                payload = json.load(source)
            except json.JSONDecodeError as error:
                raise SubmissionLedgerError(
                    f"Cannot read submission ledger {self.path}: {error}") from error
        if not isinstance(payload, dict):
            raise SubmissionLedgerError("Submission ledger must be an object.")
        if payload.get("schema_version") != LEDGER_SCHEMA_VERSION:
            raise SubmissionLedgerError(
                f"Unsupported submission ledger schema version: "
                f"{payload.get('schema_version')!r}")
        if payload.get("run_id") != self.run_id:
            raise SubmissionLedgerError(
                f"Submission ledger run ID mismatch: expected {self.run_id!r}, "
                f"found {payload.get('run_id')!r}")
        if payload.get("manifest_digest") != self.manifest_digest:
            raise SubmissionLedgerError(
                "Submission ledger manifest digest does not match this run.")
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            raise SubmissionLedgerError("Submission ledger entries must be an object.")
        self.entries = entries

    def _save(self):
        payload = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "run_id": self.run_id,
            "manifest_digest": self.manifest_digest,
            "entries": self.entries,
        }
        _atomic_write_json(self.path, payload)

    def begin(self, operation_id, operation, target, before=None):
        entry = self.entries.get(operation_id, {})
        entry.update({
            "operation": operation,
            "target": target,
            "before": before,
            "after": None,
            "status": "in_progress",
            "started_at": _now(),
            "completed_at": None,
            "attempts": int(entry.get("attempts", 0)) + 1,
        })
        self.entries[operation_id] = entry
        self._save()

    def success(self, operation_id, after=None):
        entry = self.entries.get(operation_id)
        if entry is None:
            raise SubmissionLedgerError(
                f"Cannot complete unknown ledger operation {operation_id!r}.")
        entry.update({
            "after": after,
            "status": "succeeded",
            "completed_at": _now(),
            "error": None,
        })
        self._save()

    def failure(self, operation_id, error):
        entry = self.entries.get(operation_id)
        if entry is None:
            raise SubmissionLedgerError(
                f"Cannot fail unknown ledger operation {operation_id!r}.")
        entry.update({
            "status": "failed",
            "completed_at": _now(),
            "error": str(error),
        })
        self._save()

    def has_succeeded(self, operation_id):
        entry = self.entries.get(operation_id)
        return bool(entry and entry.get("status") == "succeeded")

    def entry(self, operation_id):
        return self.entries.get(operation_id)

    def summary(self):
        counts = {}
        for entry in self.entries.values():
            status = entry.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts


@contextmanager
def submission_lock(target_id, lock_dir="tmp/submission-locks"):
    """Acquire an exclusive, nonblocking lock for one submission target."""
    safe_target_id = re.sub(r"[^A-Za-z0-9_.-]", "_", target_id)
    path = os.path.join(lock_dir, f"{safe_target_id}.lock")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SubmissionLockError(
                f"Submission target {target_id!r} is already in progress.") from error
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
