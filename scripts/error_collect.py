#!/usr/bin/env python3
"""Collect error IDs for retry."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter
from pipeline_status import all_statuses


def main():
    all_ids_file = "tmp/pipeline-all-ids.txt"
    if not os.path.exists(all_ids_file):
        print("No pipeline-all-ids.txt found", file=sys.stderr)
        sys.exit(1)

    with open(all_ids_file) as f:
        all_ids = [line.strip() for line in f if line.strip()]

    error_ids = []
    statuses = all_statuses()
    for strat_id in all_ids:
        status = statuses.get(strat_id)
        if status and status["status"] == "skipped":
            continue
        if status and status["status"] == "invalid":
            error_ids.append(strat_id)
            continue
        decomp = f"artifacts/epic-tasks/{strat_id}-decomposition.md"
        review = f"artifacts/epic-reviews/{strat_id}-decomp-review.md"

        if not os.path.exists(decomp):
            error_ids.append(strat_id)
            continue

        if os.path.exists(review):
            data, _ = read_frontmatter(review)
            if data and data.get("error"):
                error_ids.append(strat_id)

    if error_ids:
        os.makedirs("tmp", exist_ok=True)
        with open("tmp/pipeline-retry-ids.txt", "w") as f:
            for eid in error_ids:
                f.write(f"{eid}\n")

        # Set up retry batch
        with open("tmp/pipeline-batch-1-ids.txt", "w") as f:
            for eid in error_ids:
                f.write(f"{eid}\n")

        # Read current state and update
        import yaml
        state_file = "tmp/pipeline-state.yaml"
        if os.path.exists(state_file):
            with open(state_file) as f:
                state = yaml.safe_load(f)
            state["retry_cycle"] = state.get("retry_cycle", 0) + 1
            state["batch"] = 0
            state["total_batches"] = 1
            with open(state_file, "w") as f:
                yaml.dump(state, f, default_flow_style=False, sort_keys=False)

    print(f"ERRORS={len(error_ids)}")
    if error_ids:
        print(f"Retry IDs: {' '.join(error_ids)}")


if __name__ == "__main__":
    main()
