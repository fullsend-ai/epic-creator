#!/usr/bin/env python3
"""Submit decomposed epics to Jira RHAI project.

Reads epic artifacts from artifacts/epic-tasks/, creates Epic issues in
the RHAI project, sets Blocks links for dependencies, and labels the
source RHAISTRAT.

Designed for idempotent re-runs: each epic's Jira key is written to
frontmatter immediately after creation. On re-run, epics with an
existing jira_key are skipped. If a creation fails mid-batch, re-run
picks up where it left off.

Usage:
    python scripts/submit.py [--dry-run | --resume | --abort]
        [--artifacts-dir DIR] RHAISTRAT-ID ...
    python scripts/submit.py [--dry-run | --resume | --abort]
        [--artifacts-dir DIR] --all

Modes:
    --dry-run  Validate and print the plan without Jira API calls.
    --resume   Continue an existing protected submission from its ledger.
    --abort    Read-only report of an existing submission ledger.

Environment variables:
    JIRA_SERVER  Jira server URL (e.g. https://mysite.atlassian.net)
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token
"""

import argparse
import glob
import http.client
import os
import re
import sys

import yaml

# Ensure progress output is visible immediately in CI pipelines.
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import (
    read_frontmatter_validated,
    update_frontmatter,
    ValidationError,
)
from jira_utils import (
    JiraReadError,
    safe_error_message,
    require_env,
    verify_jira_auth,
    has_existing_epics,
    add_attachment,
    api_call,
    create_issue,
    create_issue_link,
    add_labels,
    get_issue,
    markdown_to_adf,
    sanitize_log_message,
    strip_metadata,
)
from submission_manifest import (
    SubmissionManifestError,
    approved_source_digest,
    approved_source_snapshot,
    build_manifest,
    manifest_path,
    manifest_digest,
    read_manifest,
    write_manifest,
)
from submission_ledger import (
    SubmissionLedger,
    SubmissionLedgerError,
    SubmissionLockError,
    ledger_path,
    submission_lock,
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


def _load_valid_components():
    """Load the canonical RHAI component list from disk.

    Returns a set of valid component names, or None if the file
    is missing (with a warning). The distinction matters: None means
    validation is unavailable (component passthrough), whereas an
    empty set would mean no valid components exist.
    """
    if not os.path.exists(COMPONENTS_PATH):
        print(f"  WARNING: {COMPONENTS_PATH} not found — "
              f"run scripts/fetch_components.py to populate",
              file=sys.stderr)
        return None
    with open(COMPONENTS_PATH) as f:
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


def _build_plan(epics, valid_components):
    """Build submission plan from epic artifacts.

    Returns list of plan entries with all fields needed for submission.
    """
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

        # Labels — all prefixed with epic-creator- for namespace clarity
        labels = ["epic-creator-auto-created"]
        if epic_type == "Investigation":
            labels.append("epic-creator-investigation")
        impl_type = data.get("implementation_type")
        if impl_type:
            labels.append(f"epic-creator-impl-{impl_type}")
        ai_impl = data.get("ai_implementability")
        if ai_impl:
            labels.append(
                f"epic-creator-ai-impl-{ai_impl.lower()}")
        if valid_components is not None and not component:
            labels.append("epic-creator-needs-component")
            print(f"  WARNING: {epic_id} component "
                  f"{raw_component!r} not in RHAI component list",
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


def _create_epics(server, user, token, plan, strat_id, assignee_id=None,
                  ledger=None):
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

        operation_id = f"create:{epic_id}"
        if ledger and ledger.has_succeeded(operation_id):
            recorded_entry = ledger.entry(operation_id) or {}
            recorded_key = (recorded_entry.get("after") or {}).get("jira_key")
            if not recorded_key:
                print(f"  ERROR: Ledger has no Jira key for {epic_id}",
                      file=sys.stderr)
                errors += 1
                continue
            if entry["jira_key"] and entry["jira_key"] != recorded_key:
                print(f"  ERROR: Jira key mismatch for {epic_id}: "
                      f"artifact={entry['jira_key']}, ledger={recorded_key}",
                      file=sys.stderr)
                errors += 1
                id_to_jira_key.pop(epic_id, None)
                continue
            if not entry["jira_key"]:
                try:
                    update_frontmatter(entry["path"],
                                       {"jira_key": recorded_key},
                                       "epic-task")
                except Exception as error:
                    print(f"  ERROR: Cannot restore {recorded_key} to "
                          f"{entry['path']}: {error}", file=sys.stderr)
                    errors += 1
                    continue
            id_to_jira_key[epic_id] = recorded_key
            print(f"  SKIP {recorded_key} <- {epic_id} (ledger)")
            continue

        recorded_entry = ledger.entry(operation_id) if ledger else None
        if recorded_entry and recorded_entry.get("status") != "succeeded":
            print(f"  ERROR: Create operation for {epic_id} is "
                  f"{recorded_entry.get('status')!r}; its Jira outcome is "
                  "ambiguous. Reconcile the ledger and Jira state manually "
                  "before retrying.", file=sys.stderr)
            errors += 1
            id_to_jira_key.pop(epic_id, None)
            break

        # Skip already-created epics (idempotent resume)
        if entry["jira_key"]:
            print(f"  SKIP {entry['jira_key']} <- {epic_id} "
                  f"(already created)")
            continue

        try:
            if ledger:
                ledger.begin(operation_id, "create_epic", epic_id,
                             before={"jira_key": None})
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
                retry_transient=False,
            )
            if ledger:
                ledger.success(operation_id,
                               after={"jira_key": jira_key})
        except Exception as e:
            if ledger:
                ledger.failure(operation_id, e)
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


def _resolve_jira_keys(plan, ledger):
    effective_keys = {}
    conflicts = []
    for entry in plan:
        epic_id = entry["epic_id"]
        artifact_key = entry.get("jira_key")
        ledger_key = None
        if ledger:
            operation_id = f"create:{epic_id}"
            if ledger.has_succeeded(operation_id):
                recorded_entry = ledger.entry(operation_id) or {}
                ledger_key = (recorded_entry.get("after") or {}).get("jira_key")
                if not isinstance(ledger_key, str) or not ledger_key:
                    conflicts.append(f"{epic_id}: ledger has no Jira key")
                    continue
        if artifact_key and ledger_key and artifact_key != ledger_key:
            conflicts.append(
                f"{epic_id}: artifact={artifact_key}, ledger={ledger_key}")
            continue
        effective_keys[epic_id] = artifact_key or ledger_key
    return effective_keys, conflicts


def _validate_reused_jira_targets(server, user, token, effective_keys,
                                  strat_id):
    for epic_id, jira_key in effective_keys.items():
        if not jira_key:
            continue
        try:
            issue = get_issue(
                server, user, token, jira_key,
                fields=["project", "issuetype", "parent"])
        except (JiraReadError, OSError, ValueError,
                http.client.HTTPException) as error:
            return (f"Cannot verify {jira_key} for {epic_id}: "
                    f"{safe_error_message(error)}")
        if not isinstance(issue, dict):
            return f"Jira returned malformed data for {jira_key}"
        fields = issue.get("fields") if isinstance(issue, dict) else None
        project = fields.get("project") if isinstance(fields, dict) else None
        issue_type = (fields.get("issuetype")
                      if isinstance(fields, dict) else None)
        parent = fields.get("parent") if isinstance(fields, dict) else None
        project_key = project.get("key") if isinstance(project, dict) else None
        type_name = (issue_type.get("name")
                     if isinstance(issue_type, dict) else None)
        parent_key = parent.get("key") if isinstance(parent, dict) else None
        if (issue.get("key") != jira_key
                or project_key != TARGET_PROJECT
                or type_name != TARGET_ISSUE_TYPE
                or parent_key != strat_id):
            return (f"Jira key {jira_key} for {epic_id} is not an "
                    f"{TARGET_PROJECT} {TARGET_ISSUE_TYPE} under {strat_id}")
    return None


def _create_dependency_links(server, user, token, plan, id_to_jira_key,
                             ledger=None):
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
            operation_id = f"link:{blocker_key}:{blocked_key}"
            if ledger and ledger.has_succeeded(operation_id):
                print(f"  SKIP link {blocker_key} -> {blocked_key} (ledger)")
                continue
            try:
                if ledger:
                    ledger.begin(operation_id, "create_issue_link",
                                 f"{blocker_key}->{blocked_key}",
                                 before={"exists": False})
                # blocker "blocks" blocked:
                # inward = blocker (shows "blocks X")
                # outward = blocked (shows "is blocked by X")
                create_issue_link(
                    server, user, token,
                    type_name=DEPENDENCY_LINK_TYPE,
                    inward_key=blocker_key,
                    outward_key=blocked_key,
                )
                if ledger:
                    ledger.success(operation_id,
                                   after={"exists": True})
                print(f"  Linked: {blocker_key} blocks {blocked_key}")
            except Exception as e:
                if ledger:
                    ledger.failure(operation_id, e)
                print(f"  ERROR linking {blocker_key} -> {blocked_key}: "
                      f"{e}", file=sys.stderr)
                errors += 1
    return errors



def _label_strategy(server, user, token, strat_id, ledger=None):
    """Apply the auto-decomposed label to the source strategy.

    Returns True on success, False on failure.
    """
    operation_id = f"label:{strat_id}"
    if ledger and ledger.has_succeeded(operation_id):
        print(f"  SKIP label {strat_id} (ledger)")
        return True
    try:
        if ledger:
            ledger.begin(operation_id, "label_strategy", strat_id,
                         before={"label": STRAT_LABEL})
        add_labels(server, user, token, strat_id, [STRAT_LABEL])
        if ledger:
            ledger.success(operation_id,
                           after={"labels": [STRAT_LABEL]})
        print(f"  Labeled {strat_id} with {STRAT_LABEL}")
        return True
    except Exception as e:
        if ledger:
            ledger.failure(operation_id, e)
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
                         id_to_jira_key, ledger=None, branch_plans=None):
    """Attach conditional branch plans to their gating Investigation epics.

    Each branch becomes a single markdown file attached to the Investigation
    epic that gates it.  Returns error count.
    """
    branches = _scan_branch_epics(artifacts_dir, strat_id)
    if branch_plans is not None:
        try:
            _validate_branch_plans(branches, branch_plans)
        except SubmissionManifestError as error:
            print(f"  ERROR: Branch plans do not match the manifest: "
                  f"{error}", file=sys.stderr)
            return 1
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
        operation_id = f"attach_branch:{jira_key}:{branch}"
        if ledger and ledger.has_succeeded(operation_id):
            print(f"  SKIP branch attachment {branch} on {jira_key} (ledger)")
            continue
        try:
            if ledger:
                ledger.begin(operation_id, "attach_branch_plan",
                             jira_key, before=None)
            filename = f"{jira_key}-branch-{branch.lower()}-plan.md"
            existing = _get_existing_attachments(
                server, user, token, jira_key)
            content = _build_branch_plan_md(epics)
            action = _replace_attachment(
                server, user, token, jira_key, filename,
                content, existing)
            if ledger:
                ledger.success(operation_id,
                               after={"filename": filename})
            print(f"  {action.title()} {filename} on {jira_key} "
                  f"({len(epics)} conditional epics)")
        except Exception as e:
            if ledger:
                ledger.failure(operation_id, e)
            print(f"  ERROR attaching branch {branch} plan to "
                  f"{jira_key}: {e}", file=sys.stderr)
            errors += 1
    return errors


def _validate_branch_plans(branches, branch_plans):
    actual = {}
    for (branch, gating_epic_id), branch_epics in branches.items():
        for path, data, _body in branch_epics:
            actual[data["epic_id"]] = {
                "branch": branch,
                "gating_epic_id": gating_epic_id,
                "source_path": path,
                "source_sha256": approved_source_digest(path),
            }
    expected = {
        entry["epic_id"]: {
            key: entry[key]
            for key in ("branch", "gating_epic_id", "source_path",
                        "source_sha256")
        }
        for entry in branch_plans
    }
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        added = sorted(set(actual) - set(expected))
        changed = sorted(
            epic_id for epic_id in set(actual) & set(expected)
            if actual[epic_id] != expected[epic_id]
        )
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if added:
            details.append(f"added: {', '.join(added)}")
        if changed:
            details.append(f"changed: {', '.join(changed)}")
        raise SubmissionManifestError("; ".join(details) or "branch plans differ")


def _has_completed_ledger_create(plan, ledger):
    if ledger is None:
        return False
    return any(
        ledger.has_succeeded(f"create:{entry['epic_id']}")
        for entry in plan
    )


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


def _attach_frontmatter(server, user, token, plan, id_to_jira_key,
                        ledger=None, source_hashes=None):
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

        operation_id = f"attach_frontmatter:{jira_key}"
        if ledger and ledger.has_succeeded(operation_id):
            print(f"  SKIP frontmatter attachment on {jira_key} (ledger)")
            continue
        try:
            if ledger:
                ledger.begin(operation_id, "attach_frontmatter",
                             jira_key, before=None)
            data, _body, source_digest = approved_source_snapshot(
                entry["path"])
            expected_digest = (source_hashes or {}).get(epic_id)
            if expected_digest is not None and source_digest != expected_digest:
                raise SubmissionManifestError(
                    f"Source artifact changed for {epic_id}")
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
            if ledger:
                ledger.success(operation_id,
                               after={"filename": filename})
            print(f"  {action.title()} {filename} on {jira_key}")
        except Exception as e:
            if ledger:
                ledger.failure(operation_id, e)
            print(f"  ERROR attaching frontmatter to {jira_key}: {e}",
                  file=sys.stderr)
            errors += 1
    return errors


def _submit_strategy(server, user, token, strat_id, plan, dry_run,
                     artifacts_dir="artifacts", ledger=None,
                     branch_plans=None, source_hashes=None):
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

    if branch_plans is not None:
        try:
            _validate_branch_plans(
                _scan_branch_epics(artifacts_dir, strat_id), branch_plans)
        except SubmissionManifestError as error:
            print(f"  ERROR: Branch plans do not match the manifest: "
                  f"{error}", file=sys.stderr)
            return 0, 1

    effective_keys, key_conflicts = _resolve_jira_keys(plan, ledger)
    if key_conflicts:
        print("  ERROR: Artifact Jira keys conflict with the ledger: "
              + "; ".join(key_conflicts), file=sys.stderr)
        print("  No Jira writes were made in this attempt.", file=sys.stderr)
        return 0, 1

    failure = None
    recovery = (
        "Check Jira credentials and access, then retry the same submission with "
        "its checks enabled. Preserve the original artifacts and recorded "
        "jira_key values for partial resumes.")
    try:
        verify_jira_auth(server, user, token)
        # A fresh breakdown must still be eligible at the actual write boundary.
        # Recorded keys identify partial submissions that this script can resume.
        if (not any(entry["jira_key"] for entry in plan)
                and not _has_completed_ledger_create(plan, ledger)):
            strategy = get_issue(server, user, token, strat_id,
                                 fields=["issuelinks"])
            if not isinstance(strategy, dict) or strategy.get("key") != strat_id:
                raise JiraReadError("Jira did not return the requested strategy.")
            if has_existing_epics(strategy, server, user, token):
                failure = "Strategy already has linked epics."
                recovery = (
                    "Review the existing epics instead of submitting a fresh "
                    "breakdown. To resume a partial submission, use its original "
                    "artifacts with the recorded jira_key values.")
    except (JiraReadError, OSError, ValueError, http.client.HTTPException) as e:
        failure = safe_error_message(e)
    if failure:
        print(f"  ERROR: Submission blocked for {strat_id}: {failure}", file=sys.stderr)
        print(f"  No Jira writes were made for {strat_id} in this attempt.\n"
              f"  Report the failure. {recovery}", file=sys.stderr)
        return 0, 1

    target_failure = _validate_reused_jira_targets(
        server, user, token, effective_keys, strat_id)
    if target_failure:
        print(f"  ERROR: {target_failure}", file=sys.stderr)
        print("  No Jira writes were made in this attempt.", file=sys.stderr)
        return 0, 1

    # Fetch strategy assignee to propagate to epics
    assignee_id = _get_strat_assignee(server, user, token, strat_id)

    # Phase 1: Create epic issues as children of strategy (idempotent)
    print("  Phase 1: Creating epics...")
    id_to_jira_key, errors = _create_epics(server, user, token, plan,
                                           strat_id, assignee_id,
                                           ledger=ledger)
    created = len(id_to_jira_key) - sum(
        1 for e in plan if e["jira_key"])

    if not id_to_jira_key:
        print("  No epics created — skipping linking and labeling")
        return 0, errors

    # Phase 2: Create dependency links (for all created epics, including
    # those from prior runs — links are idempotent)
    print("  Phase 2: Creating dependency links...")
    errors += _create_dependency_links(
        server, user, token, plan, id_to_jira_key, ledger=ledger)

    # Phase 3: Attach frontmatter YAML to each epic
    print("  Phase 3: Attaching frontmatter metadata...")
    errors += _attach_frontmatter(
        server, user, token, plan, id_to_jira_key, ledger=ledger,
        source_hashes=source_hashes)

    # Phase 4: Attach conditional branch plans to investigation epics
    print("  Phase 4: Attaching branch plans...")
    errors += _attach_branch_plans(
        server, user, token, artifacts_dir, strat_id, id_to_jira_key,
        ledger=ledger, branch_plans=branch_plans)

    # Phase 5: Label source strategy (only when ALL epics are created)
    all_created = len(id_to_jira_key) == len(plan)
    if all_created:
        print("  Phase 5: Labeling source strategy...")
        if not _label_strategy(server, user, token, strat_id,
                               ledger=ledger):
            errors += 1
    else:
        print(f"  Phase 5: SKIP labeling — "
              f"{len(plan) - len(id_to_jira_key)} epics not yet created")

    return created, errors


def _load_existing_submission(artifacts_dir, strat_id, manifest):
    manifest_file = manifest_path(artifacts_dir, strat_id)
    if not os.path.exists(manifest_file):
        raise SubmissionManifestError(
            f"No submission manifest found for {strat_id}.")
    existing_manifest = read_manifest(manifest_file)
    if manifest_digest(existing_manifest) != manifest_digest(manifest):
        raise SubmissionManifestError(
            f"Submission manifest for {strat_id} has changed since the run "
            "was created.")
    ledger_file = ledger_path(artifacts_dir, existing_manifest["run_id"])
    if not os.path.exists(ledger_file):
        raise SubmissionLedgerError(
            f"No submission ledger found for {existing_manifest['run_id']}.")
    ledger = SubmissionLedger(
        ledger_file, existing_manifest["run_id"],
        manifest_digest(existing_manifest))
    return existing_manifest, ledger


def _build_current_manifest(strat_id, plan, review_data, review_path,
                            artifacts_dir):
    return build_manifest(
        strat_id, plan, review_data, review_path,
        _scan_branch_epics(artifacts_dir, strat_id))


def _run_submission(server, user, token, strat_id, plan, dry_run,
                    artifacts_dir, manifest, ledger):
    _print_plan(plan, strat_id, dry_run)
    return _submit_strategy(
        server, user, token, strat_id, plan, dry_run,
        artifacts_dir=artifacts_dir, ledger=ledger,
        branch_plans=manifest.get("branch_plans", []),
        source_hashes={
            entry["epic_id"]: entry["source_sha256"]
            for entry in manifest.get("epics", [])
        })


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("strat_ids", nargs="*", metavar="RHAISTRAT-ID",
                        help="Strategy IDs to submit epics for")
    parser.add_argument("--all", action="store_true",
                        help="Submit all strategies with passing reviews")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true",
                            help="Print planned actions without making API calls")
    mode_group.add_argument("--resume", action="store_true",
                            help="Resume an existing submission from its ledger")
    mode_group.add_argument("--abort", action="store_true",
                            help="Report an existing submission without making API calls")
    parser.add_argument("--artifacts-dir", default="artifacts",
                        help="Artifacts directory (default: artifacts)")
    args = parser.parse_args()

    if not args.strat_ids and not args.all:
        parser.error("Provide RHAISTRAT IDs or use --all")

    if args.abort:
        server = user = token = None
    else:
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
        review_path = os.path.join(args.artifacts_dir, "epic-reviews",
                                   f"{strat_id}-decomp-review.md")

        try:
            manifest = _build_current_manifest(
                strat_id, plan, review_data, review_path, args.artifacts_dir)
        except SubmissionManifestError as error:
            print(f"  ERROR: Submission manifest invalid for {strat_id}: "
                  f"{sanitize_log_message(error)}", file=sys.stderr)
            total_errors += 1
            continue
        if args.abort:
            try:
                existing_manifest, ledger = _load_existing_submission(
                    args.artifacts_dir, strat_id, manifest)
            except (SubmissionManifestError, SubmissionLedgerError) as error:
                print(f"  ERROR: Cannot abort {strat_id}: "
                      f"{sanitize_log_message(error)}",
                      file=sys.stderr)
                total_errors += 1
                continue
            print(f"  Abort requested for run {existing_manifest['run_id']}")
            print(f"  Ledger summary: {ledger.summary()}")
            print("  No Jira writes were made in this attempt.")
            continue
        if args.dry_run:
            _print_plan(plan, strat_id, args.dry_run)
            created, errors = _submit_strategy(
                server, user, token, strat_id, plan, args.dry_run,
                artifacts_dir=args.artifacts_dir,
                branch_plans=manifest.get("branch_plans", []))
            total_created += created
            total_errors += errors
            continue

        try:
            with submission_lock(strat_id):
                passed, review_data = _check_review_passed(
                    args.artifacts_dir, strat_id)
                if not passed:
                    print("  SKIP: Review did not pass (or not found)")
                    total_skipped += 1
                    continue
                epics = _scan_epics(args.artifacts_dir, strat_id)
                if not epics:
                    print("  SKIP: No epic artifacts found")
                    total_skipped += 1
                    continue
                plan = _build_plan(epics, valid_components)
                review_path = os.path.join(
                    args.artifacts_dir, "epic-reviews",
                    f"{strat_id}-decomp-review.md")
                manifest = _build_current_manifest(
                    strat_id, plan, review_data, review_path,
                    args.artifacts_dir)
                existing_manifest_path = manifest_path(
                    args.artifacts_dir, strat_id)
                if args.resume or os.path.exists(existing_manifest_path):
                    manifest, ledger = _load_existing_submission(
                        args.artifacts_dir, strat_id, manifest)
                    created, errors = _run_submission(
                        server, user, token, strat_id, plan, args.dry_run,
                        args.artifacts_dir, manifest, ledger)
                else:
                    digest = manifest_digest(manifest)
                    write_manifest(manifest_path(args.artifacts_dir, strat_id),
                                   manifest)
                    ledger = SubmissionLedger(
                        ledger_path(args.artifacts_dir, manifest["run_id"]),
                        manifest["run_id"], digest)
                    created, errors = _run_submission(
                        server, user, token, strat_id, plan, args.dry_run,
                        args.artifacts_dir, manifest, ledger)
                total_created += created
                total_errors += errors
        except (SubmissionManifestError, SubmissionLedgerError,
                SubmissionLockError) as error:
            print(f"  ERROR: {sanitize_log_message(error)}",
                  file=sys.stderr)
            total_errors += 1

    # ── Summary ──
    print(f"\n{'='*60}")
    print(f"Summary: {total_created} epics created, "
          f"{total_skipped} strategies skipped, "
          f"{total_errors} errors")
    if total_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
