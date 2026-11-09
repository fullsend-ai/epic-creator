#!/usr/bin/env python3
"""Generate run report for a decomposition pipeline run."""

import argparse
import os
import sys
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter
from review_utils import load_review, unchanged_review_recoveries


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

    results = []
    for strat_id in ids:
        entry = {"strat_id": strat_id, "status": "missing"}
        decomp = f"artifacts/epic-tasks/{strat_id}-decomposition.md"
        review = f"artifacts/epic-reviews/{strat_id}-decomp-review.md"

        if os.path.exists(decomp):
            data, _ = read_frontmatter(decomp)
            entry["epic_count"] = data.get("epic_count", 0)
            entry["status"] = "decomposed"

        if os.path.exists(decomp) or os.path.exists(review):
            status, data = load_review(review)
            entry["status"] = status
            if status == "error":
                entry["error"] = data["error"]
            else:
                entry["score"] = data.get("score")

        # A passing retry is a recovery event, not proof of a false negative.
        entry["review_recoveries_without_edits"] = (
            unchanged_review_recoveries(strat_id))

        results.append(entry)

    report = {
        "started": opts.start_time,
        "completed": end_time,
        "batch_size": opts.batch_size,
        "total": len(ids),
        "results": results,
        "recovered_without_edits": [
            entry["strat_id"] for entry in results
            if entry["review_recoveries_without_edits"] > 0
        ],
    }

    os.makedirs("artifacts/decompose-runs", exist_ok=True)
    ts = opts.start_time.replace(":", "-")
    report_path = f"artifacts/decompose-runs/{ts}.yaml"
    with open(report_path, "w") as f:
        yaml.dump(report, f, default_flow_style=False, sort_keys=False)

    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
