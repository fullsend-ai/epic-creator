#!/usr/bin/env python3
"""Check epic components against the canonical RHAI component list.

Runs before submission so a non-canonical component name can be corrected
while the decomposition is still being reviewed and revised. submit.py
strips any name not in .context/rhai-components.txt and adds the
epic-creator-needs-component label; this check catches those names earlier
and suggests the closest canonical names.

An epic for which no canonical component fits sets `needs_component=true`
in its frontmatter. That is an explicit request for human review, not an
error: submit.py leaves the Jira component unset and adds
epic-creator-needs-component.

Usage:
    python3 scripts/check_components.py [--artifacts-dir DIR] RHAISTRAT-ID ...

Exit codes:
    0  every epic component is canonical or explicitly flagged
       (also when the component list is missing: validation is skipped)
    1  at least one epic needs a component correction
    2  usage error
"""

import argparse
import difflib
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter

COMPONENTS_PATH = os.path.join(".context", "rhai-components.txt")

MAX_SUGGESTIONS = 3

STATUS_VALID = "valid"
STATUS_INVALID = "invalid"
STATUS_FLAGGED = "needs_component"
STATUS_CONFLICT = "conflict"


def load_components(path=COMPONENTS_PATH):
    """Return the set of canonical component names, or None if missing."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def suggest_components(name, valid_components, limit=MAX_SUGGESTIONS):
    """Return up to `limit` canonical names closest to `name`, best first.

    Tries, in order: a case-insensitive exact match, the name with any
    parenthetical removed (and the parenthetical text itself), canonical
    names contained in the name or containing it, and finally fuzzy
    string similarity.
    """
    name = (name or "").strip()
    if not name or not valid_components:
        return []
    by_lower = {}
    for comp in sorted(valid_components):
        by_lower.setdefault(comp.lower(), comp)

    suggestions = []

    def add(comp):
        if comp and comp not in suggestions:
            suggestions.append(comp)

    lower = name.lower()
    add(by_lower.get(lower))

    stripped = re.sub(r"\s*\([^)]*\)", "", name).strip().lower()
    add(by_lower.get(stripped))
    for inner in re.findall(r"\(([^)]*)\)", name):
        add(by_lower.get(inner.strip().lower()))

    # Containment needs a minimum length so short names like "AI" do not
    # match almost everything.
    for comp_lower, comp in by_lower.items():
        if len(comp_lower) >= 3 and (comp_lower in lower
                                     or (len(lower) >= 3
                                         and lower in comp_lower)):
            add(comp)

    for match in difflib.get_close_matches(
            stripped or lower, list(by_lower), n=limit, cutoff=0.6):
        add(by_lower[match])

    return suggestions[:limit]


def check_component(component, needs_component, valid_components):
    """Classify one epic's component.

    Returns (status, suggestions), where status is one of:
      valid            component is in the canonical list
      invalid          component is not canonical and not flagged
      needs_component  flagged: no canonical component fits (human review)
      conflict         flagged, but the component is canonical
    """
    component = component.strip() if isinstance(component, str) else ""
    in_list = component in valid_components
    if needs_component:
        return (STATUS_CONFLICT if in_list else STATUS_FLAGGED), []
    if in_list:
        return STATUS_VALID, []
    return STATUS_INVALID, suggest_components(component, valid_components)


def find_epic_files(artifacts_dir, strat_id):
    """Return main-plan and branch epic files for a strategy, sorted."""
    pattern = os.path.join(artifacts_dir, "epic-tasks",
                           f"{strat_id}-*E[0-9][0-9][0-9].md")
    return sorted(glob.glob(pattern))


def check_strategy(artifacts_dir, strat_id, valid_components):
    """Check every epic file of a strategy.

    Returns a list of dicts with path, epic_id, component, status and
    suggestions.
    """
    results = []
    for path in find_epic_files(artifacts_dir, strat_id):
        data, _ = read_frontmatter(path)
        component = data.get("component")
        status, suggestions = check_component(
            component, data.get("needs_component") is True, valid_components)
        results.append({
            "path": path,
            "epic_id": data.get("epic_id") or os.path.basename(path)[:-3],
            "component": component if isinstance(component, str) else "",
            "status": status,
            "suggestions": suggestions,
        })
    return results


def format_result(result):
    """Return an actionable message for a result, or None if valid."""
    epic_id = result["epic_id"]
    component = result["component"]
    path = result["path"]
    status = result["status"]
    if status == STATUS_INVALID:
        lines = [f"INVALID {epic_id}: component {component!r} is not in "
                 f"{COMPONENTS_PATH}."]
        if result["suggestions"]:
            names = ", ".join(repr(s) for s in result["suggestions"])
            lines.append(f"  Closest canonical names: {names}")
            lines.append(f"  Fix: python3 scripts/frontmatter.py set {path} "
                         f"component=\"{result['suggestions'][0]}\"")
        else:
            lines.append("  No close canonical name found. Pick the owning "
                         "or parent-level component from the list.")
        lines.append(f"  If no canonical component fits: "
                     f"python3 scripts/frontmatter.py set {path} "
                     f"needs_component=true")
        return "\n".join(lines)
    if status == STATUS_CONFLICT:
        return (f"CONFLICT {epic_id}: needs_component=true but component "
                f"{component!r} is canonical.\n"
                f"  Fix: python3 scripts/frontmatter.py set {path} "
                f"needs_component=false")
    if status == STATUS_FLAGGED:
        return (f"FLAGGED {epic_id}: needs_component=true (component "
                f"{component!r}). Jira component will be left unset and "
                f"epic-creator-needs-component added for human review.")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("strat_ids", nargs="+", metavar="RHAISTRAT-ID",
                        help="Strategy IDs whose epics to check")
    parser.add_argument("--artifacts-dir", default="artifacts",
                        help="Artifacts directory (default: artifacts)")
    args = parser.parse_args(argv)

    valid_components = load_components()
    if valid_components is None:
        print(f"WARNING: {COMPONENTS_PATH} not found — component check "
              f"skipped (run scripts/fetch_components.py to populate)",
              file=sys.stderr)
        return 0

    counts = {STATUS_VALID: 0, STATUS_INVALID: 0, STATUS_FLAGGED: 0,
              STATUS_CONFLICT: 0}
    for strat_id in args.strat_ids:
        for result in check_strategy(args.artifacts_dir, strat_id,
                                     valid_components):
            counts[result["status"]] += 1
            message = format_result(result)
            if message:
                print(message)

    print(f"Component check: {counts[STATUS_VALID]} valid, "
          f"{counts[STATUS_INVALID]} invalid, "
          f"{counts[STATUS_CONFLICT]} conflicting, "
          f"{counts[STATUS_FLAGGED]} flagged for human review")
    return 1 if counts[STATUS_INVALID] or counts[STATUS_CONFLICT] else 0


if __name__ == "__main__":
    sys.exit(main())
