#!/usr/bin/env python3
"""Summarize decomposition results for a batch."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter
from review_utils import load_review


def main():
    counts_only = "--counts-only" in sys.argv
    ids = [arg for arg in sys.argv[1:] if not arg.startswith("--")]

    if not ids:
        print("No IDs provided", file=sys.stderr)
        sys.exit(1)

    decomposed = 0
    reviewed = 0
    passed = 0
    failed = 0
    errors = 0
    total_epics = 0

    for strat_id in ids:
        decomp = f"artifacts/epic-tasks/{strat_id}-decomposition.md"
        review = f"artifacts/epic-reviews/{strat_id}-decomp-review.md"

        if os.path.exists(decomp):
            decomposed += 1
            data, _ = read_frontmatter(decomp)
            if data:
                total_epics += data.get("epic_count", 0)

        if os.path.exists(review):
            reviewed += 1
        status, _ = load_review(review)
        if status == "error":
            errors += 1
        elif status == "passed":
            passed += 1
        else:
            failed += 1

    if counts_only:
        print(f"decomposed={decomposed} reviewed={reviewed} "
              f"passed={passed} failed={failed} errors={errors} "
              f"total_epics={total_epics}")
    else:
        print(f"Batch summary ({len(ids)} strategies):")
        print(f"  Decomposed: {decomposed}/{len(ids)}")
        print(f"  Reviewed:   {reviewed}/{decomposed}")
        print(f"  Passed:     {passed}")
        print(f"  Failed:     {failed}")
        print(f"  Errors:     {errors}")
        print(f"  Total epics generated: {total_epics}")


if __name__ == "__main__":
    main()
