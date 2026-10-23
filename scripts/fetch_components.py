#!/usr/bin/env python3
"""Fetch RHAI project components from Jira and write to disk.

Writes the canonical component list to .context/rhai-components.txt
(one component name per line, sorted). This file is read by the
decomposer prompt to constrain component selection, and by submit.py
to validate before creating issues.

Set EPIC_SKIP_COMPONENT_FETCH to preserve a pre-provisioned component list.

Usage:
    python scripts/fetch_components.py
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(__file__))
from jira_utils import require_env, api_call_with_retry
from workload import get_workload, WorkloadError

PROJECT = "RHAI"
OUTPUT_PATH = os.path.join(".context", "rhai-components.txt")


def fetch_components(server, user, token, project=PROJECT):
    """Fetch all component names from the RHAI project.

    Validates that names are non-empty and contain no newlines
    (which would corrupt the one-per-line file format).
    """
    components = api_call_with_retry(
        server, f"/project/{project}/components", user, token)
    names = []
    for c in components:
        name = c.get("name", "").strip()
        if not name or "\n" in name:
            print(f"  WARNING: Skipping invalid component name: {name!r}",
                  file=sys.stderr)
            continue
        names.append(name)
    return sorted(names)


def main(args=None):
    # args=[] preserves existing programmatic callers; CLI passes argv below.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", help="Use the selected planning component project/cache")
    opts = parser.parse_args([] if args is None else args)
    project, output = PROJECT, OUTPUT_PATH
    if opts.workload:
        selected = get_workload(opts.workload)
        if selected.planning is None:
            raise WorkloadError("Selected workload has no component policy")
        project, output = selected.planning.component_project, selected.planning.component_file
    if os.environ.get("EPIC_SKIP_COMPONENT_FETCH"):
        print("EPIC_SKIP_COMPONENT_FETCH set - skipping component fetch")
        return

    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars "
              "required.", file=sys.stderr)
        sys.exit(1)

    try:
        names = fetch_components(server, user, token, project)
    except Exception as e:
        print(f"Error fetching components: {e}", file=sys.stderr)
        sys.exit(1)

    if not names:
        print(f"Error: No components fetched from {project} project.",
              file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w") as f:
        for name in names:
            f.write(f"{name}\n")

    print(f"Wrote {len(names)} components to {output}")


if __name__ == "__main__":
    main(sys.argv[1:])
