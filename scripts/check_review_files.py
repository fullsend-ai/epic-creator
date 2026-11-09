#!/usr/bin/env python3
"""List exact epic/rationale paths and check them without model file discovery."""
import argparse
import json
from pathlib import Path
import re

import yaml

from artifact_utils import read_frontmatter


def check_review_files(strat_id, artifacts_dir="artifacts"):
    if not re.fullmatch(r"RHAISTRAT-\d+", strat_id):
        raise ValueError("Expected a RHAISTRAT issue key")
    directory = Path(artifacts_dir) / "epic-tasks"
    pattern = re.compile(rf"{strat_id}-(?:BRANCH-[A-Z]+-)?E\d{{3}}\.md")
    actual = {p.stem for p in directory.glob(f"{strat_id}-*.md")
              if pattern.fullmatch(p.name)}
    errors = []
    summary_ids = set()
    try:
        data, body = read_frontmatter(str(directory / f"{strat_id}-decomposition.md"))
        count = data.get("epic_count")
        main = {name for name in actual if "-BRANCH-" not in name}
        if type(count) is not int or count < 1:
            errors.append("Decomposition summary needs a positive epic_count")
        elif count not in (len(main), len(actual)):
            # The generation contract permits either main-only or all-epic
            # counts when conditional branches are present.
            errors.append(f"epic_count {count} matches neither {len(main)} main "
                          f"nor {len(actual)} total epic files")
        # The summary's Epic List identifies files that may be absent entirely.
        # Read exact table ID cells, not incidental prose or DAG edge labels.
        for line in body.splitlines():
            if not line.lstrip().startswith("|"):
                continue
            cell = line.strip().split("|")[1].strip().strip("`*")
            match = re.fullmatch(rf"(?:{strat_id}-)?((?:BRANCH-[A-Z]+-)?E\d{{3}})", cell)
            if match:
                summary_ids.add(f"{strat_id}-{match.group(1)}")
        summary_main = {name for name in summary_ids if "-BRANCH-" not in name}
        if summary_main and summary_main != main:
            errors.append("Main epic files differ from the summary's Epic List")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"Cannot read decomposition summary: {exc}")

    epics = []
    if not actual and not summary_ids:
        errors.append("No epic files or Epic List entries found")
    for name in sorted(actual | summary_ids):
        epic = directory / f"{name}.md"
        rationale = directory / f"{name}-ai-signals.md"
        entry = {"epic_file": str(epic), "rationale_file": str(rationale),
                 "epic_readable": False, "rationale_readable": False}
        for path, field in ((epic, "epic_readable"), (rationale, "rationale_readable")):
            try:
                if not path.read_text(encoding="utf-8").strip():
                    raise ValueError("file is empty")
                entry[field] = True
            except (OSError, ValueError) as exc:
                errors.append(f"Cannot read {path}: {exc}")
        epics.append(entry)
    return {"strat_id": strat_id, "epics": epics, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("strat_id")
    parser.add_argument("--artifacts-dir", default="artifacts")
    parser.add_argument("--allow-missing", action="store_true",
                        help="Print problems for repair without a failing exit status")
    args = parser.parse_args()
    try:
        result = check_review_files(args.strat_id, args.artifacts_dir)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 1 if result["errors"] and not args.allow_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
