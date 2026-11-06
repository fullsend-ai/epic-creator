#!/usr/bin/env python3
"""Generate run report for a decomposition pipeline run."""

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter
from pipeline_workload import bound_workload, source_metadata, validate_decomposition, validate_artifact
from pipeline_status import all_statuses
from workload import WorkloadError


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

    statuses = all_statuses()
    ids = list(dict.fromkeys(ids + sorted(set(statuses) - set(ids))))

    if not ids:
        print("No IDs to report on", file=sys.stderr)
        sys.exit(1)

    end_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = None
    if os.path.exists("tmp/pipeline-state.yaml"):
        from pipeline_state import _load_state
        state = _load_state()

    results = []
    for strat_id in ids:
        entry = {"strat_id": strat_id, "status": "missing"}
        terminal = statuses.get(strat_id)
        if terminal:
            if terminal["status"] == "skipped":
                entry["status"] = "skipped"
                entry["message"] = terminal["message"]
                results.append(entry)
                continue
            if terminal["status"] == "invalid":
                entry["status"] = "invalid"
                entry["error"] = terminal["message"]
                entry["message"] = terminal["message"]
                results.append(entry)
                continue

        decomp = f"artifacts/epic-tasks/{strat_id}-decomposition.md"
        review = f"artifacts/epic-reviews/{strat_id}-decomp-review.md"
        try:
            if state and Path(f"artifacts/strat-tasks/{strat_id}.md").exists():
                entry.update(source_metadata(state, strat_id))

            if os.path.exists(decomp):
                if state:
                    validate_decomposition(state, strat_id)
                data, _ = read_frontmatter(decomp)
                entry["epic_count"] = data.get("epic_count", 0)
                entry["status"] = "decomposed"

            if os.path.exists(review):
                if state:
                    validate_artifact(state, strat_id, review, "decomp-review")
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
        except WorkloadError as exc:
            entry["status"] = "invalid"
            entry["error"] = str(exc)
            entry["message"] = str(exc)

        results.append(entry)

    report = {
        "started": opts.start_time,
        "completed": end_time,
        "batch_size": opts.batch_size,
        "total": len(ids),
        "results": results,
    }
    if state:
        selected = bound_workload(state)
        report.update(workload_id=selected.id, workload_version=selected.version,
                      workload_digest=selected.digest, planning_digest=selected.planning.digest,
                      review_pass_score=selected.planning.pass_score, review_max_score=selected.planning.max_score)

    os.makedirs("artifacts/decompose-runs", exist_ok=True)
    ts = opts.start_time.replace(":", "-")
    report_path = f"artifacts/decompose-runs/{ts}.yaml"
    with open(report_path, "w") as f:
        yaml.dump(report, f, default_flow_style=False, sort_keys=False)

    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
