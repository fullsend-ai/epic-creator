#!/usr/bin/env python3
"""Generate run report for a decomposition pipeline run."""

import argparse
import json
from pathlib import Path
from pipeline_status import all_statuses
import os
import sys
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-time", required=True)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("ids", nargs="*")
    opts = parser.parse_args()

    ids = opts.ids
    if not ids:
        ids_file = "tmp/pipeline-all-ids.txt"
        if os.path.exists(ids_file):
            with open(ids_file) as f:
                ids = [line.strip() for line in f if line.strip()]

    if not ids:
        print("No IDs to report on", file=sys.stderr)
        sys.exit(1)

    end_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    statuses = all_statuses()
    results = []
    for strat_id in ids:
        entry = {"strat_id": strat_id, "status": "missing"}
        repository_plan = Path(f"artifacts/repository-plans/{strat_id}.json")
        if repository_plan.exists():
            try:
                planning = json.loads(repository_plan.read_text())
                entry["repository_planning"] = {
                    "ready": planning.get("ready_for_drafting", False),
                    "mapping_snapshot_digest": planning.get("mapping_snapshot_digest"),
                    "scopes": planning.get("scopes", []),
                    "unresolved": planning.get("unresolved", []),
                    "artifact": str(repository_plan),
                }
            except (OSError, ValueError):
                entry["repository_planning"] = {"ready": False, "error": "Invalid repository plan"}
        decomp = f"artifacts/epic-tasks/{strat_id}-decomposition.md"
        review = f"artifacts/epic-reviews/{strat_id}-decomp-review.md"

        if os.path.exists(decomp):
            data, _ = read_frontmatter(decomp)
            entry["epic_count"] = data.get("epic_count", 0)
            entry["status"] = "decomposed"

        if os.path.exists(review):
            data, _ = read_frontmatter(review)
            if data.get("error"):
                entry["status"] = "error"
                entry["error"] = data["error"]
            elif data.get("pass"):
                entry["status"] = "passed"
                entry["score"] = data.get("score")
            else:
                entry["status"] = "failed"
                entry["score"] = data.get("score")

        terminal = statuses.get(strat_id)
        if terminal:
            entry["status"] = terminal["status"]
            entry["error"] = terminal.get("message", "Repository planning blocked")
        results.append(entry)

    report = {
        "started": opts.start_time,
        "completed": end_time,
        "batch_size": opts.batch_size,
        "total": len(ids),
        "results": results,
    }

    os.makedirs("artifacts/decompose-runs", exist_ok=True)
    ts = opts.start_time.replace(":", "-")
    report_path = f"artifacts/decompose-runs/{ts}.yaml"
    with open(report_path, "w") as f:
        yaml.dump(report, f, default_flow_style=False, sort_keys=False)

    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
