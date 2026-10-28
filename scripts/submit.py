#!/usr/bin/env python3
"""Submit decomposed epics to Jira.

With --workload, validate typed artifacts and use the descriptor's destination
and relationship policy. Keep the submission journal across retries; an unknown
create outcome stops until the existing issue can be recovered.

Without --workload, retain legacy untyped RHAISTRAT submission to RHAI Epics,
Blocks dependency links, and frontmatter-key recovery.

Usage:
    python scripts/submit.py --workload ID [--dry-run] SOURCE-ID ...
    python scripts/submit.py [--dry-run] [--artifacts-dir DIR] RHAISTRAT-ID ...
    python scripts/submit.py [--dry-run] --all

Environment variables:
    JIRA_SERVER  Jira server URL (e.g. https://mysite.atlassian.net)
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token
"""

import argparse
import glob
import os
import re
import sys

import yaml

# Ensure progress output is visible immediately in CI pipelines.
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import (
    read_frontmatter,
    read_frontmatter_validated,
    update_frontmatter,
    ValidationError,
)
from jira_utils import (
    require_env,
    add_attachment,
    api_call,
    create_issue,
    create_issue_link,
    add_labels,
    get_issue,
    markdown_to_adf,
    strip_metadata,
)


# ─── Constants ────────────────────────────────────────────────────────────────

TARGET_PROJECT = "RHAI"
TARGET_ISSUE_TYPE = "Epic"

PRIORITY_MAP = {
    "P0": "Critical",
    "P1": "Major",
    "P2": "Minor",
}

STRAT_LABEL = "epic-creator-auto-decomposed"

# Link type for epic dependencies (E002 "is blocked by" E001).
DEPENDENCY_LINK_TYPE = "Blocks"



# ─── Component Validation ────────────────────────────────────────────────────

COMPONENTS_PATH = os.path.join(".context", "rhai-components.txt")


def _load_valid_components(path=None):
    """Load the canonical RHAI component list from disk.

    Returns a set of valid component names, or None if the file
    is missing (with a warning). The distinction matters: None means
    validation is unavailable (component passthrough), whereas an
    empty set would mean no valid components exist.
    """
    path = COMPONENTS_PATH if path is None else path
    if not os.path.exists(path):
        print(f"  WARNING: {path} not found — "
              f"run scripts/fetch_components.py to populate",
              file=sys.stderr)
        return None
    with open(path) as f:
        return {line.strip() for line in f if line.strip()}


def validate_component(component, valid_components):
    """Check if a component name is in the canonical list.

    Returns the component name if valid, or None with a warning if not.
    """
    if not valid_components:
        return None  # No list available — can't validate
    if component in valid_components:
        return component
    return None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _build_description(body):
    """Build the Jira description from the epic markdown body.

    Strips frontmatter (already removed by read_frontmatter) and the
    ## Title section (title goes in the summary field).
    """
    lines = body.split("\n")
    result = []
    skip_title = False
    for line in lines:
        if line.strip() == "## Title":
            skip_title = True
            continue
        if skip_title:
            if line.strip().startswith("## "):
                skip_title = False
                # Fall through to append this next section header
            else:
                continue
        result.append(line)
    # Strip leading blank lines
    while result and result[0].strip() == "":
        result.pop(0)
    return "\n".join(result)


def _scan_epics(artifacts_dir, strat_id):
    """Find all epic artifact files for a given strategy.

    Returns list of (path, frontmatter_dict, body_string) sorted by epic_id.
    """
    pattern = os.path.join(artifacts_dir, "epic-tasks",
                           f"{strat_id}-E*.md")
    paths = sorted(glob.glob(pattern))
    epics = []
    for path in paths:
        # Skip decomposition summary and review files
        if "-decomposition" in path or "-decomp-review" in path:
            continue
        # Skip BRANCH files for now (conditional epics)
        if "-BRANCH-" in path:
            continue
        try:
            data, body = read_frontmatter_validated(path, "epic-task")
            epics.append((path, data, body))
        except ValidationError as e:
            print(f"  WARNING: Skipping {path}: {e}", file=sys.stderr)
    return epics


def _check_review_passed(artifacts_dir, strat_id):
    """Check if the decomposition review passed for a strategy.

    Returns (passed: bool, review_data: dict or None).
    """
    review_path = os.path.join(artifacts_dir, "epic-reviews",
                               f"{strat_id}-decomp-review.md")
    if not os.path.exists(review_path):
        return False, None
    try:
        data, _ = read_frontmatter_validated(review_path, "decomp-review")
        return data.get("pass", False), data
    except ValidationError as e:
        print(f"  WARNING: Cannot read review for {strat_id}: {e}",
              file=sys.stderr)
        return False, None


def _find_submittable_strats(artifacts_dir):
    """Find all strategies that have decompositions ready to submit.

    A strategy is submittable if it has:
    - A decomposition summary file
    - A passing review
    - Epic artifact files
    """
    pattern = os.path.join(artifacts_dir, "epic-tasks",
                           "*-decomposition.md")
    strat_ids = []
    for path in sorted(glob.glob(pattern)):
        basename = os.path.basename(path)
        match = re.match(r'^(RHAISTRAT-\d+)-decomposition\.md$', basename)
        if match:
            strat_ids.append(match.group(1))
    return strat_ids


def _build_plan(epics, valid_components, submission=None):
    """Build submission plan from epic artifacts.

    Returns list of plan entries with all fields needed for submission.
    """
    label_policy = submission.labels if submission else {
        "created": "epic-creator-auto-created", "investigation": "epic-creator-investigation",
        "implementation_prefix": "epic-creator-impl-", "ai_prefix": "epic-creator-ai-impl-",
        "needs_component": "epic-creator-needs-component"}
    plan = []
    for path, data, body in epics:
        epic_id = data["epic_id"]
        title = data["title"]
        priority = PRIORITY_MAP.get(data["priority"], "Major")
        raw_component = data.get("component", "")
        # Only validate when the component cache is available (not None).
        # None means the cache file is missing — pass through the
        # frontmatter value rather than stripping valid components.
        if valid_components is not None:
            component = validate_component(raw_component, valid_components)
        else:
            component = raw_component or None
        dependencies = data.get("dependencies", [])
        epic_type = data.get("type", "Implementation")
        jira_key = data.get("jira_key")

        # Labels come from the selected policy, or the legacy namespace.
        labels = [label_policy["created"]]
        if epic_type == "Investigation":
            labels.append(label_policy["investigation"])
        impl_type = data.get("implementation_type")
        if impl_type:
            labels.append(f"{label_policy['implementation_prefix']}{impl_type}")
        ai_impl = data.get("ai_implementability")
        if ai_impl:
            labels.append(
                f"{label_policy['ai_prefix']}{ai_impl.lower()}")
        if valid_components is not None and not component:
            labels.append(label_policy["needs_component"])
            print(f"  WARNING: {epic_id} component "
                  f"{raw_component!r} not in {submission.project if submission else 'RHAI'} component list",
                  file=sys.stderr)

        plan.append({
            "epic_id": epic_id,
            "title": title,
            "priority": priority,
            "component": component,
            "raw_component": raw_component,
            "labels": labels,
            "dependencies": dependencies,
            "path": path,
            "body": body,
            "jira_key": jira_key,
        })

    return plan


def _print_plan(plan, strat_id, dry_run):
    """Print the submission plan table."""
    already = sum(1 for e in plan if e["jira_key"])
    remaining = len(plan) - already

    print(f"\n  {'Epic ID':<30} {'Priority':<10} {'Component':<30} "
          f"{'Status'}")
    print(f"  {'-'*80}")
    for entry in plan:
        comp_display = entry["component"] or f"({entry['raw_component']})"
        status = entry["jira_key"] or "pending"
        print(f"  {entry['epic_id']:<30} {entry['priority']:<10} "
              f"{comp_display:<30} {status}")
        if entry["dependencies"]:
            print(f"  {'':>30} blocked by: "
                  f"{', '.join(entry['dependencies'])}")

    if already:
        print(f"\n  Resume: {already} already created, "
              f"{remaining} remaining")
    print()


def _get_strat_assignee(server, user, token, strat_id):
    """Fetch the assignee account ID from a strategy issue.

    Returns the accountId string or None if unassigned.
    """
    try:
        issue = get_issue(server, user, token, strat_id,
                          fields=["assignee"])
        assignee = issue.get("fields", {}).get("assignee")
        if assignee:
            display = assignee.get("displayName", assignee["accountId"])
            print(f"  Strategy assignee: {display}")
            return assignee["accountId"]
        print("  Strategy assignee: (unassigned)")
        return None
    except Exception as e:
        print(f"  WARNING: Could not fetch assignee for {strat_id}: {e}",
              file=sys.stderr)
        return None


def _create_epics(server, user, token, plan, strat_id, assignee_id=None):
    """Create epic issues in Jira. Writes jira_key to frontmatter on success.

    Stops on first failure — already-created epics (jira_key in frontmatter)
    are skipped. Returns (id_to_jira_key dict, error_count).
    """
    id_to_jira_key = {}
    errors = 0

    # Seed with already-created epics from prior runs
    for entry in plan:
        if entry["jira_key"]:
            id_to_jira_key[entry["epic_id"]] = entry["jira_key"]

    for entry in plan:
        epic_id = entry["epic_id"]

        # Skip already-created epics (idempotent resume)
        if entry["jira_key"]:
            print(f"  SKIP {entry['jira_key']} <- {epic_id} "
                  f"(already created)")
            continue

        try:
            # Build description ADF
            desc_md = _build_description(entry["body"])
            desc_adf = markdown_to_adf(desc_md)

            # Components list (empty if no mapping)
            components = [entry["component"]] if entry["component"] else []

            jira_key = create_issue(
                server, user, token,
                project=TARGET_PROJECT,
                issue_type=TARGET_ISSUE_TYPE,
                summary=entry["title"],
                description_adf=desc_adf,
                priority=entry["priority"],
                labels=entry["labels"],
                components=components,
                parent_key=strat_id,
                assignee_id=assignee_id,
            )
        except Exception as e:
            print(f"  ERROR creating {epic_id}: {e}", file=sys.stderr)
            print(f"  Stopping epic creation for this strategy. "
                  f"Re-run to resume.", file=sys.stderr)
            errors += 1
            break  # Stop on first failure — don't create orphans

        # Persist jira_key to frontmatter immediately — this is the
        # durable marker that makes re-runs safe.  Separated from the
        # create_issue try-block so a frontmatter write failure doesn't
        # discard the returned jira_key (which would cause duplicates).
        try:
            update_frontmatter(entry["path"],
                               {"jira_key": jira_key}, "epic-task")
        except Exception as e:
            print(f"  ERROR persisting jira_key {jira_key} for "
                  f"{epic_id}: {e}", file=sys.stderr)
            print(f"  WARNING: Epic was created in Jira but "
                  f"frontmatter not updated. Manually set "
                  f"jira_key={jira_key} in {entry['path']} before "
                  f"re-running to avoid duplicates.",
                  file=sys.stderr)
            id_to_jira_key[epic_id] = jira_key
            errors += 1
            break  # Stop — frontmatter is out of sync

        id_to_jira_key[epic_id] = jira_key
        print(f"  Created {jira_key} <- {epic_id}: "
              f"{entry['title'][:50]}")

    return id_to_jira_key, errors


def _create_dependency_links(server, user, token, plan, id_to_jira_key):
    """Create Blocks links between epics for DAG dependencies.

    Skips links where either end wasn't created. Link creation is
    idempotent in Jira (duplicate links are harmless).
    Returns error count.
    """
    errors = 0
    for entry in plan:
        epic_id = entry["epic_id"]
        blocked_key = id_to_jira_key.get(epic_id)
        if not blocked_key:
            continue

        for dep_id in entry["dependencies"]:
            blocker_key = id_to_jira_key.get(dep_id)
            if not blocker_key:
                print(f"  WARNING: Cannot link {epic_id} -> {dep_id}: "
                      f"dependency not created", file=sys.stderr)
                continue
            try:
                # blocker "blocks" blocked:
                # inward = blocker (shows "blocks X")
                # outward = blocked (shows "is blocked by X")
                create_issue_link(
                    server, user, token,
                    type_name=DEPENDENCY_LINK_TYPE,
                    inward_key=blocker_key,
                    outward_key=blocked_key,
                )
                print(f"  Linked: {blocker_key} blocks {blocked_key}")
            except Exception as e:
                print(f"  ERROR linking {blocker_key} -> {blocked_key}: "
                      f"{e}", file=sys.stderr)
                errors += 1
    return errors



def _label_strategy(server, user, token, strat_id):
    """Apply the auto-decomposed label to the source strategy.

    Returns True on success, False on failure.
    """
    try:
        add_labels(server, user, token, strat_id, [STRAT_LABEL])
        print(f"  Labeled {strat_id} with {STRAT_LABEL}")
        return True
    except Exception as e:
        print(f"  ERROR labeling {strat_id}: {e}", file=sys.stderr)
        return False


def _get_existing_attachments(server, user, token, jira_key):
    """Return dict of {filename: attachment_id} for a Jira issue."""
    issue = get_issue(server, user, token, jira_key,
                      fields=["attachment"])
    attachments = issue.get("fields", {}).get("attachment", [])
    return {a["filename"]: a["id"] for a in attachments}


def _scan_branch_epics(artifacts_dir, strat_id):
    """Find conditional branch epic files for a strategy.

    Returns dict mapping (branch_letter, gating_epic_id) to a list of
    (path, frontmatter_dict, body_string) tuples, sorted by epic_id.
    """
    pattern = os.path.join(artifacts_dir, "epic-tasks",
                           f"{strat_id}-BRANCH-*-E*.md")
    paths = sorted(glob.glob(pattern))
    branches = {}
    for path in paths:
        try:
            data, body = read_frontmatter_validated(path, "epic-task")
        except ValidationError as e:
            print(f"  WARNING: Skipping {path}: {e}", file=sys.stderr)
            continue
        branch = data.get("branch")
        gated_by = data.get("gated_by")
        if not branch or not gated_by:
            print(f"  WARNING: Branch file {path} missing branch or "
                  f"gated_by field", file=sys.stderr)
            continue
        key = (branch, gated_by)
        branches.setdefault(key, []).append((path, data, body))
    return branches


def _build_branch_plan_md(branch_epics):
    """Combine branch epic files into a single markdown document.

    Each epic appears as a section with its full frontmatter and body,
    separated by a horizontal rule.
    """
    sections = []
    for path, data, body in branch_epics:
        yaml_str = yaml.dump(
            data, default_flow_style=False, sort_keys=False,
            allow_unicode=True)
        sections.append(f"---\n{yaml_str}---\n{body}")
    return "\n\n---\n\n".join(sections)


def _attach_branch_plans(server, user, token, artifacts_dir, strat_id,
                         id_to_jira_key):
    """Attach conditional branch plans to their gating Investigation epics.

    Each branch becomes a single markdown file attached to the Investigation
    epic that gates it.  Returns error count.
    """
    branches = _scan_branch_epics(artifacts_dir, strat_id)
    if not branches:
        return 0

    errors = 0
    for (branch, gating_epic_id), epics in sorted(branches.items()):
        jira_key = id_to_jira_key.get(gating_epic_id)
        if not jira_key:
            print(f"  WARNING: Cannot attach branch {branch} plan — "
                  f"gating epic {gating_epic_id} not in Jira",
                  file=sys.stderr)
            continue
        try:
            filename = f"{jira_key}-branch-{branch.lower()}-plan.md"
            existing = _get_existing_attachments(
                server, user, token, jira_key)
            content = _build_branch_plan_md(epics)
            action = _replace_attachment(
                server, user, token, jira_key, filename,
                content, existing)
            print(f"  {action.title()} {filename} on {jira_key} "
                  f"({len(epics)} conditional epics)")
        except Exception as e:
            print(f"  ERROR attaching branch {branch} plan to "
                  f"{jira_key}: {e}", file=sys.stderr)
            errors += 1
    return errors


def _replace_attachment(server, user, token, jira_key, filename, content,
                        existing):
    """Upload an attachment, replacing any prior version.

    existing is a {filename: attachment_id} dict from
    _get_existing_attachments.  Returns "attached" | "replaced" | "skipped"
    to indicate what happened (skipped only on empty content).
    """
    if filename in existing:
        api_call(server, f"/attachment/{existing[filename]}",
                 user, token, method="DELETE")
    add_attachment(server, user, token, jira_key, filename, content)
    return "replaced" if filename in existing else "attached"


def _attach_frontmatter(server, user, token, plan, id_to_jira_key):
    """Attach epic frontmatter YAML to each Jira epic.

    Preserves structured metadata (gate relationships, AI signals, etc.)
    that isn't captured by Jira fields, links, or labels.
    Replaces existing attachment if present (handles re-decomposition).
    Returns error count.
    """
    errors = 0
    for entry in plan:
        epic_id = entry["epic_id"]
        jira_key = id_to_jira_key.get(epic_id)
        if not jira_key:
            continue

        try:
            data, _ = read_frontmatter(entry["path"])
            if not data:
                continue
            filename = f"{jira_key}-frontmatter.yaml"
            existing = _get_existing_attachments(
                server, user, token, jira_key)
            yaml_content = yaml.dump(
                data, default_flow_style=False, sort_keys=False,
                allow_unicode=True)
            action = _replace_attachment(
                server, user, token, jira_key, filename,
                yaml_content, existing)
            print(f"  {action.title()} {filename} on {jira_key}")
        except Exception as e:
            print(f"  ERROR attaching frontmatter to {jira_key}: {e}",
                  file=sys.stderr)
            errors += 1
    return errors


def _submit_strategy(server, user, token, strat_id, plan, dry_run,
                     artifacts_dir="artifacts"):
    """Execute all submission phases for one strategy.

    Returns (created_count, error_count).
    """
    if dry_run:
        already = [e for e in plan if e["jira_key"]]
        pending = [e for e in plan if not e["jira_key"]]
        if already:
            print("  [DRY RUN] Already created:")
            for entry in already:
                print(f"    - {entry['jira_key']} <- {entry['epic_id']}")
        print("  [DRY RUN] Would create:")
        for entry in pending:
            print(f"    - {entry['epic_id']}: {entry['title'][:60]}")
        print("  [DRY RUN] Would attach frontmatter to each epic")
        print(f"  [DRY RUN] Would label {strat_id} with {STRAT_LABEL}")
        return len(pending), 0

    # Fetch strategy assignee to propagate to epics
    assignee_id = _get_strat_assignee(server, user, token, strat_id)

    # Phase 1: Create epic issues as children of strategy (idempotent)
    print("  Phase 1: Creating epics...")
    id_to_jira_key, errors = _create_epics(server, user, token, plan,
                                           strat_id, assignee_id)
    created = len(id_to_jira_key) - sum(
        1 for e in plan if e["jira_key"])

    if not id_to_jira_key:
        print("  No epics created — skipping linking and labeling")
        return 0, errors

    # Phase 2: Create dependency links (for all created epics, including
    # those from prior runs — links are idempotent)
    print("  Phase 2: Creating dependency links...")
    errors += _create_dependency_links(
        server, user, token, plan, id_to_jira_key)

    # Phase 3: Attach frontmatter YAML to each epic
    print("  Phase 3: Attaching frontmatter metadata...")
    errors += _attach_frontmatter(
        server, user, token, plan, id_to_jira_key)

    # Phase 4: Attach conditional branch plans to investigation epics
    print("  Phase 4: Attaching branch plans...")
    errors += _attach_branch_plans(
        server, user, token, artifacts_dir, strat_id, id_to_jira_key)

    # Phase 5: Label source strategy (only when ALL epics are created)
    all_created = len(id_to_jira_key) == len(plan)
    if all_created:
        print("  Phase 5: Labeling source strategy...")
        if not _label_strategy(server, user, token, strat_id):
            errors += 1
    else:
        print(f"  Phase 5: SKIP labeling — "
              f"{len(plan) - len(id_to_jira_key)} epics not yet created")

    return created, errors


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("strat_ids", nargs="*", metavar="SOURCE-ID",
                        help="Source issue IDs to submit epics for")
    parser.add_argument("--all", action="store_true",
                        help="Submit all strategies with passing reviews")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned actions without making API calls")
    parser.add_argument("--artifacts-dir", default="artifacts",
                        help="Artifacts directory (default: artifacts)")
    parser.add_argument("--workload", help="Validated workload for typed artifact submission")
    args = parser.parse_args()

    if not args.strat_ids and not args.all:
        parser.error("Provide source issue IDs or use --all")

    if args.workload:
        from workload_submit import run_submission
        sys.exit(run_submission(args))

    server, user, token = require_env()
    if not args.dry_run and not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars "
              "required.", file=sys.stderr)
        print("Set these or use --dry-run for local-only validation.",
              file=sys.stderr)
        sys.exit(1)

    # Load canonical component list for validation
    valid_components = _load_valid_components()

    # Determine which strategies to submit
    if args.all:
        epic_tasks_dir = os.path.join(args.artifacts_dir, "epic-tasks")
        if not os.path.isdir(epic_tasks_dir):
            print(f"Error: {epic_tasks_dir} is not a directory.",
                  file=sys.stderr)
            sys.exit(1)
        strat_ids = _find_submittable_strats(args.artifacts_dir)
        if not strat_ids:
            # An empty batch (e.g. every strategy already has epics) is a
            # normal outcome for scheduled runs, not a failure.
            print("No decompositions found. Nothing to submit.")
            return
    else:
        strat_ids = args.strat_ids

    total_created = 0
    total_skipped = 0
    total_errors = 0

    for strat_id in strat_ids:
        print(f"\n{'='*60}")
        print(f"Strategy: {strat_id}")
        print(f"{'='*60}")

        # Typed artifacts must never enter the compatibility submission path.
        candidates = [os.path.join(args.artifacts_dir, "strat-tasks", f"{strat_id}.md"),
                      os.path.join(args.artifacts_dir, "epic-reviews", f"{strat_id}-decomp-review.md")]
        candidates += glob.glob(os.path.join(args.artifacts_dir, "epic-tasks", f"{strat_id}-*.md"))
        if any(read_frontmatter(path)[0].get("workload_id") for path in candidates):
            print("Error: typed artifacts require explicit --workload selection", file=sys.stderr)
            sys.exit(1)

        # Check review status
        passed, review_data = _check_review_passed(args.artifacts_dir,
                                                   strat_id)
        if not passed:
            print(f"  SKIP: Review did not pass (or not found)")
            total_skipped += 1
            continue

        # Load epic artifacts
        epics = _scan_epics(args.artifacts_dir, strat_id)
        if not epics:
            print(f"  SKIP: No epic artifacts found")
            total_skipped += 1
            continue

        print(f"  Review: score={review_data.get('score')}, pass=True")
        print(f"  Epics: {len(epics)}")

        # Build submission plan
        plan = _build_plan(epics, valid_components)
        _print_plan(plan, strat_id, args.dry_run)

        # Execute submission
        created, errors = _submit_strategy(
            server, user, token, strat_id, plan, args.dry_run,
            artifacts_dir=args.artifacts_dir)
        total_created += created
        total_errors += errors

    # ── Summary ──
    print(f"\n{'='*60}")
    print(f"Summary: {total_created} epics created, "
          f"{total_skipped} strategies skipped, "
          f"{total_errors} errors")
    if total_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
