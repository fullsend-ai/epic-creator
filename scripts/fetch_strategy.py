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

sys.path.insert(0, os.path.dirname(__file__))
from jira_utils import (JiraReadError, require_env, get_issue, verify_jira_auth,
                        search_issues_page, has_existing_epics as _has_existing_epics,
                        adf_to_markdown, download_attachment, safe_error_message)

import re
import yaml


def _fetch_issue(server, user, token, key):
    fields = ["summary", "description", "labels", "issuelinks", "status",
              "priority", "attachment"]
    return get_issue(server, user, token, key, fields=fields)


def _find_strategy_attachment(attachments, key):
    """Find the best strategy-content attachment from the attachment list.

    Matches RHAISTRAT-NNNN-strategy.md.
    When multiple exist (e.g. re-uploaded revisions), returns the most
    recently created one.
    Returns (filename, content_url, size) or None.
    """
    candidates = []
    for a in (attachments or []):
        fn = a.get("filename", "")
        if re.match(r'^RHAISTRAT-\d+-strategy\.md$', fn):
            candidates.append(a)
    if not candidates:
        return None
    # Pick the most recently uploaded revision
    best = max(candidates, key=lambda a: a.get("created", ""))
    return best["filename"], best["content"], best.get("size", 0)


def _write_strategy(issue_data, output_dir="artifacts/strat-tasks",
                    server=None, user=None, token=None):
    key = issue_data["key"]
    fields = issue_data["fields"]
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
    attach_info = _find_strategy_attachment(attachments, key)
    if attach_info and server and user and token:
        attach_fn, attach_url, attach_size = attach_info
        try:
            attach_content = download_attachment(attach_url, user, token,
                                                   server=server)
            description = attach_content.decode("utf-8", errors="replace").strip()
            attachment_source = attach_fn
            print(f"  {key}: using attachment {attach_fn} "
                  f"({attach_size // 1024}KB) as strategy body",
                  file=sys.stderr)
        except (urllib.error.HTTPError, urllib.error.URLError,
                ValueError, UnicodeDecodeError) as e:
            print(f"  {key}: WARNING: failed to download attachment "
                  f"{attach_fn}: {e}", file=sys.stderr)

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
    fields = "summary,description,labels,issuelinks,status,priority,attachment"
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
    parser.add_argument("--skip-if-has-epics", action="store_true",
                        help="Skip strategies that already have linked epics")
    parser.add_argument("--data-dir", help="Data directory (unused, for compat)")
    opts = parser.parse_args(args)

    # A failed fetch must not leave IDs from an earlier run available to use.
    try:
        os.unlink(opts.ids_file)
    except FileNotFoundError:
        pass

    server, user, token = require_env()
    if not all([server, user, token]):
        raise JiraReadError("JIRA_SERVER, JIRA_USER, and JIRA_TOKEN must be set.")

    verify_jira_auth(server, user, token)
    issues = _search_issues(server, user, token, opts.jql, opts.limit)

    selected = []
    skipped = 0
    failed_checks = 0
    for issue in issues:
        key = issue["key"]
        if opts.skip_if_has_epics:
            try:
                has_epics = _has_existing_epics(issue, server, user, token)
            except (JiraReadError, ValueError, OSError, http.client.HTTPException) as e:
                print(f"WARNING: Skipped {key}: could not check existing epics: "
                      f"{safe_error_message(e)}", file=sys.stderr)
                failed_checks += 1
                continue
            if has_epics:
                print(f"Skipped {key}: already has linked epics", file=sys.stderr)
                skipped += 1
                continue
        selected.append(issue)

    if skipped:
        print(f"Skipped {skipped} strategies with existing epics", file=sys.stderr)
    if failed_checks:
        print(f"Skipped {failed_checks} strategies because existing epics "
              "could not be checked", file=sys.stderr)
        if not selected:
            raise JiraReadError(
                "No strategies fetched and some existing-epic checks failed.")

    # Complete all skip checks before writing any new strategy inputs or IDs.
    for issue in selected:
        key = issue["key"]
        _write_strategy(issue, server=server, user=user, token=token)
        print(f"Fetched {key}: {issue['fields'].get('summary', '')[:60]}",
              file=sys.stderr)

    ids_dir = os.path.dirname(opts.ids_file) or "."
    os.makedirs(ids_dir, exist_ok=True)
    ids_tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=ids_dir,
                                         prefix=".strategy-ids-", delete=False) as f:
            ids_tmp = f.name
            for issue in selected:
                f.write(f"{issue['key']}\n")
        os.replace(ids_tmp, opts.ids_file)
    finally:
        if ids_tmp is not None and os.path.exists(ids_tmp):
            os.unlink(ids_tmp)

    print(f"Fetched {len(selected)} strategies", file=sys.stderr)
    if not selected:
        print("No work to decompose.", file=sys.stderr)


def cmd_fetch_one(args):
    if not args:
        print("Usage: fetch-one <RHAISTRAT-ID>", file=sys.stderr)
        sys.exit(1)
    key = args[0]
    server, user, token = require_env()
    if not all([server, user, token]):
        raise JiraReadError("JIRA_SERVER, JIRA_USER, and JIRA_TOKEN must be set.")
    issue = _fetch_issue(server, user, token, key)
    if not issue:
        raise JiraReadError(f"Jira did not return strategy {key}.")
    path = _write_strategy(issue, server=server, user=user, token=token)
    print(path)


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
    except (JiraReadError, OSError, ValueError, http.client.HTTPException) as e:
        print(f"ERROR: Fetch failed: {safe_error_message(e)}", file=sys.stderr)
        if sys.argv[1] == "fetch":
            print("Fetch aborted; no IDs file published.", file=sys.stderr)
        print("Stop this run and report the failure. Check Jira access and the "
              "reported error, then retry the same command with its checks enabled.\n"
              "Do not continue with saved IDs or strategy artifacts.",
              file=sys.stderr)
        sys.exit(1)
