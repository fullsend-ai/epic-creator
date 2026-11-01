#!/usr/bin/env python3
"""Fetch RHAISTRAT strategies from Jira REST API.

Usage:
    python3 scripts/fetch_strategy.py fetch "<JQL>" --ids-file <path> [--limit N]
    python3 scripts/fetch_strategy.py fetch-one <RHAISTRAT-ID>
"""

import http.client
import os
import sys
import tempfile
import urllib.error
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from jira_utils import (JiraReadError, require_env, get_issue, verify_jira_auth,
                        search_issues_page, has_existing_epics as _has_existing_epics,
                        adf_to_markdown, download_attachment, safe_error_message)

import re
import yaml
from artifact_utils import ValidationError, parse_frontmatter, write_frontmatter
from pipeline_status import clear_status, write_status
from workload import WorkloadError, get_workload, load_registry, resolve_issue


def _fetch_issue(server, user, token, key):
    fields = ["summary", "description", "labels", "issuelinks", "status",
              "priority", "attachment", "project", "issuetype", "updated"]
    return get_issue(server, user, token, key, fields=fields)


def _select_strategy_attachment(attachments, key, workload=None):
    """Find the best strategy-content attachment from the attachment list.

    The legacy path matches RHAISTRAT-NNNN-strategy.md.
    When multiple exist (e.g. re-uploaded revisions), returns the most
    recently created one.
    Returns the attachment record or None. Descriptor intake matches the
    exact source key and compares timestamps with their timezone offsets.
    """
    candidates = []
    for a in (attachments or []):
        fn = a.get("filename", "")
        matches = (fn == workload.attachment_template.format(key=key) if workload
                   else re.match(r'^RHAISTRAT-\d+-strategy\.md$', fn))
        if matches:
            candidates.append(a)
    if not candidates:
        return None
    # Pick the most recently uploaded revision
    if workload:
        def created(attachment):
            try:
                timestamp = attachment["created"].replace("Z", "+00:00")
                timestamp = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", timestamp)
                value = datetime.fromisoformat(timestamp)
                if value.tzinfo is None:
                    raise ValueError("missing timezone")
                return value
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                raise WorkloadError(f"{key}: invalid attachment timestamp") from exc
        return max(candidates, key=created)
    return max(candidates, key=lambda a: a.get("created", ""))


def _find_strategy_attachment(attachments, key):
    """Legacy tuple interface retained for existing callers."""
    best = _select_strategy_attachment(attachments, key)
    if best is None:
        return None
    return best["filename"], best["content"], best.get("size", 0)


def _write_strategy(issue_data, output_dir="artifacts/strat-tasks",
                    server=None, user=None, token=None, workload=None):
    key = issue_data["key"]
    fields = issue_data["fields"]
    if workload:
        workload.validate_ready(issue_data)
        if not fields.get("updated"):
            raise WorkloadError(f"{key}: missing Jira source revision")
    title = fields.get("summary", "")
    desc_raw = fields.get("description")
    labels = fields.get("labels", [])
    status = fields.get("status", {}).get("name", "")
    priority = fields.get("priority", {}).get("name", "")

    if isinstance(desc_raw, dict):
        description = adf_to_markdown(desc_raw).strip()
    elif desc_raw:
        description = str(desc_raw).strip()
    else:
        description = ""

    # Check for strategy-content attachment (full strategy when description
    # was truncated due to Jira's size limit)
    attachment_source = None
    attachments = fields.get("attachment", [])
    attachment = _select_strategy_attachment(attachments, key, workload)
    if workload and not workload.description_fallback and attachment is None:
        print(f"Skipped {key}: no strategy attachment", file=sys.stderr)
        return None
    if workload and attachment and not all([server, user, token]):
        raise WorkloadError(f"{key}: credentials are required to read its strategy attachment")
    if workload and attachment and not all(attachment.get(name) for name in ("id", "content", "created")):
        raise WorkloadError(f"{key}: strategy attachment has incomplete revision metadata")
    if attachment and server and user and token:
        attach_fn, attach_url, attach_size = (attachment["filename"], attachment["content"],
                                             attachment.get("size", 0))
        try:
            attach_content = download_attachment(attach_url, user, token,
                                                   server=server)
            description = attach_content.decode("utf-8", errors="strict" if workload else "replace").strip()
            attachment_source = attach_fn
            print(f"  {key}: using attachment {attach_fn} "
                  f"({attach_size // 1024}KB) as strategy body",
                  file=sys.stderr)
        except (urllib.error.HTTPError, urllib.error.URLError,
                ValueError, UnicodeDecodeError) as e:
            if workload and not workload.description_fallback:
                raise WorkloadError(f"{key}: failed to read strategy attachment: {e}") from e
            print(f"  {key}: WARNING: failed to download attachment "
                  f"{attach_fn}: {e}", file=sys.stderr)

    if workload and attachment_source:
        if not description:
            raise WorkloadError(f"{key}: empty strategy attachment")
        try:
            metadata, body = parse_frontmatter(description, strict=True)
        except (yaml.YAMLError, ValidationError) as exc:
            raise WorkloadError(f"{key}: malformed attachment frontmatter") from exc
        workload.validate_handoff(metadata, key)
        if not body.strip():
            raise WorkloadError(f"{key}: strategy attachment has no body")

    links = []
    for link in fields.get("issuelinks", []):
        if "outwardIssue" in link:
            target = link["outwardIssue"]["key"]
            direction = link["type"].get("outward", "")
            links.append(f"{direction} {target}")
        elif "inwardIssue" in link:
            target = link["inwardIssue"]["key"]
            direction = link["type"].get("inward", "")
            links.append(f"{direction} {target}")

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{key}.md")

    frontmatter = {
        "strat_id": key,
        "title": title,
        "status": status,
        "priority": priority,
        "labels": labels if labels else [],
        "links": links if links else [],
    }
    if attachment_source:
        frontmatter["attachment_source"] = attachment_source

    if workload:
        frontmatter.update(workload_id=workload.id, workload_version=workload.version,
                           workload_digest=workload.digest,
                           source_key=key, source_project=workload.project,
                           source_issue_type=workload.issue_type,
                           source_revision=fields["updated"])
        if workload.planning:
            frontmatter["planning_digest"] = workload.planning.digest
        if attachment_source:
            frontmatter.update(attachment_id=str(attachment.get("id", "")),
                               attachment_created=attachment["created"])
        write_frontmatter(path, frontmatter, "strat-task", body=f"\n{description}\n")
        return path

    with open(path, "w") as f:
        f.write("---\n")
        f.write(yaml.dump(frontmatter, default_flow_style=False,
                          sort_keys=False, allow_unicode=True))
        f.write("---\n\n")
        f.write(description)
        f.write("\n")

    return path


def _search_issues(server, user, token, jql, limit=100):
    """Search for issues using JQL with pagination via /search/jql."""
    all_issues = []
    fields = "summary,description,labels,issuelinks,status,priority,attachment,project,issuetype,updated"
    next_page_token = None
    seen_tokens = set()

    while len(all_issues) < limit:
        batch_size = min(50, limit - len(all_issues))
        issues, next_page_token = search_issues_page(
            server, user, token, jql, max_results=batch_size, fields=fields,
            next_page_token=next_page_token)
        all_issues.extend(issues)
        if next_page_token is None:
            break
        if not issues or next_page_token in seen_tokens:
            raise JiraReadError("Jira strategy search did not make pagination progress.")
        seen_tokens.add(next_page_token)

    return all_issues


def cmd_fetch(args):
    import argparse
    parser = argparse.ArgumentParser(prog="fetch_strategy.py fetch")
    parser.add_argument("jql", help="JQL query string")
    parser.add_argument("--ids-file", required=True,
                        help="Output file for fetched IDs")
    parser.add_argument("--limit", type=int, default=100,
                        help="Max issues to fetch")
    parser.add_argument("--skip-if-has-epics", "--skip-if-processed", action="store_true",
                        help="Skip sources already processed under the selected intake policy")
    parser.add_argument("--workload", help="Opt in to a registered intake workload")
    parser.add_argument("--data-dir", help="Data directory (unused, for compat)")
    opts = parser.parse_args(args)

    # A failed fetch must not leave IDs from an earlier run available to use.
    try:
        os.unlink(opts.ids_file)
    except FileNotFoundError:
        pass

    registry = load_registry() if opts.workload else None
    if registry is not None:
        get_workload(opts.workload, registry)

    server, user, token = require_env()
    if not all([server, user, token]):
        raise JiraReadError("JIRA_SERVER, JIRA_USER, and JIRA_TOKEN must be set.")

    verify_jira_auth(server, user, token)
    issues = _search_issues(server, user, token, opts.jql, opts.limit)

    selected = []
    skipped = 0
    failed_checks = 0
    for issue in issues:
        key = issue.get("key")
        if not key:
            print("ERROR: Skipped issue without a Jira key", file=sys.stderr)
            skipped += 1
            continue
        clear_status(key)
        try:
            workload = resolve_issue(issue, opts.workload, registry) if registry is not None else None
        except WorkloadError as exc:
            write_status(key, "invalid", "fetch", str(exc))
            print(f"ERROR: Skipped {key}: {exc}", file=sys.stderr)
            skipped += 1
            continue

        if opts.skip_if_has_epics:
            try:
                if workload and workload.skip_policy == "processed-label":
                    processed = workload.processed_label in (issue["fields"].get("labels") or [])
                else:
                    processed = _has_existing_epics(issue, server, user, token)
            except (JiraReadError, ValueError, OSError, http.client.HTTPException) as exc:
                message = safe_error_message(exc)
                write_status(key, "invalid", "fetch", message)
                print(f"WARNING: Skipped {key}: could not check existing epics: "
                      f"{message}", file=sys.stderr)
                failed_checks += 1
                continue
            if processed:
                write_status(key, "skipped", "fetch", "already processed")
                print(f"Skipped {key}: already processed", file=sys.stderr)
                skipped += 1
                continue
        selected.append((issue, workload))

    if failed_checks:
        print(f"Skipped {failed_checks} strategies because existing epics "
              "could not be checked", file=sys.stderr)
        if not selected:
            raise JiraReadError(
                "No strategies fetched and some existing-epic checks failed.")

    # Complete all skip checks before writing any new strategy inputs or IDs.
    all_keys = []
    for issue, workload in selected:
        key = issue["key"]
        try:
            options = {"workload": workload} if workload is not None else {}
            path = _write_strategy(issue, server=server, user=user, token=token, **options)
        except WorkloadError as exc:
            if workload is None:
                raise
            write_status(key, "invalid", "fetch", str(exc))
            print(f"ERROR: Skipped {key}: {exc}", file=sys.stderr)
            skipped += 1
            continue
        if path is None and workload is not None:
            write_status(key, "skipped", "fetch", "no strategy attachment")
            skipped += 1
            continue
        all_keys.append(key)
        print(f"Fetched {key}: {issue['fields'].get('summary', '')[:60]}",
              file=sys.stderr)

    if failed_checks and not all_keys:
        raise JiraReadError(
            "No strategies fetched and some existing-epic checks failed.")

    ids_dir = os.path.dirname(opts.ids_file) or "."
    os.makedirs(ids_dir, exist_ok=True)
    ids_tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=ids_dir,
                                         prefix=".strategy-ids-", delete=False) as f:
            ids_tmp = f.name
            for key in all_keys:
                f.write(f"{key}\n")
        os.replace(ids_tmp, opts.ids_file)
    finally:
        if ids_tmp is not None and os.path.exists(ids_tmp):
            os.unlink(ids_tmp)

    if skipped:
        suffix = " with existing epics" if registry is None else ""
        print(f"Skipped {skipped} strategies{suffix}", file=sys.stderr)
    print(f"Fetched {len(all_keys)} strategies", file=sys.stderr)
    if not all_keys:
        print("No work to decompose.", file=sys.stderr)


def cmd_fetch_one(args):
    import argparse
    parser = argparse.ArgumentParser(prog="fetch_strategy.py fetch-one")
    parser.add_argument("key", help="Source strategy issue key")
    parser.add_argument("--workload", help="Opt in to a registered intake workload")
    opts = parser.parse_args(args)
    key = opts.key
    clear_status(key)
    try:
        registry = load_registry() if opts.workload else None
        if registry is not None:
            get_workload(opts.workload, registry)
        server, user, token = require_env()
        if not all([server, user, token]):
            raise WorkloadError("JIRA_SERVER, JIRA_USER, and JIRA_TOKEN must be set")
        issue = _fetch_issue(server, user, token, key)
        if not issue:
            raise WorkloadError(f"{key}: Jira returned no issue")
        selected = resolve_issue(issue, opts.workload, registry) if registry is not None else None
        path = _write_strategy(issue, server=server, user=user, token=token,
                               workload=selected)
        if path is None:
            write_status(key, "skipped", "fetch", "no strategy attachment")
            print(f"SKIPPED {key}: no strategy attachment", file=sys.stderr)
            return
        print(path)
    except Exception as exc:
        message = str(exc) if isinstance(exc, WorkloadError) else safe_error_message(exc)
        write_status(key, "invalid", "fetch", message)
        raise


COMMANDS = {
    "fetch": cmd_fetch,
    "fetch-one": cmd_fetch_one,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Commands: {', '.join(COMMANDS)}", file=sys.stderr)
        sys.exit(1)
    try:
        COMMANDS[sys.argv[1]](sys.argv[2:])
    except (JiraReadError, OSError, ValueError, http.client.HTTPException) as exc:
        message = str(exc) if isinstance(exc, WorkloadError) else safe_error_message(exc)
        print(f"ERROR: Fetch failed: {message}", file=sys.stderr)
        if sys.argv[1] == "fetch":
            print("Fetch aborted; no IDs file published.", file=sys.stderr)
        print("Stop this run and report the failure. Check Jira access and the "
              "reported error, then retry the same command with its checks enabled.\n"
              "Do not continue with saved IDs or strategy artifacts.",
              file=sys.stderr)
        sys.exit(1)
