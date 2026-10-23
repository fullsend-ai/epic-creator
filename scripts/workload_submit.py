"""Explicit workload submission with local preflight and conservative recovery.

Jira labels are searchable identities, not uniqueness constraints. Callers must
serialize independent workspaces and retain artifacts/submissions/ across retries.
"""
from contextlib import contextmanager
import fcntl
from graphlib import TopologicalSorter, CycleError
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

import yaml

import artifact_utils as artifacts
from fetch_strategy import _select_strategy_attachment
import jira_utils as jira
from pipeline_workload import source_metadata, validate_artifact, validate_decomposition
import workload
from workload import WorkloadError


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def preflight(selected, key, root):
    """Validate every local artifact, including conditional branches, before writes."""
    from submit import _build_plan, _build_description

    state = {"workload_id": selected.id, "workload_version": selected.version,
             "workload_digest": selected.digest, "planning_digest": selected.planning.digest,
             "workload_explicit": True}
    metadata = source_metadata(state, key, root)
    validate_decomposition(state, key, root)
    review = validate_artifact(state, key, root / f"epic-reviews/{key}-decomp-review.md",
                               "decomp-review", root)
    if not review["pass"]:
        raise WorkloadError(f"{key}: review did not pass")

    main, branches = [], {}
    for path in sorted((root / "epic-tasks").glob(f"{key}-*.md")):
        if path.name == f"{key}-decomposition.md" or path.name.endswith("-ai-signals.md"):
            continue
        data = validate_artifact(state, key, path, "epic-task", root)
        if re.fullmatch(rf"{re.escape(key)}-E\d{{3}}", path.stem):
            if data.get("branch"):
                raise WorkloadError(f"{path}: main epic cannot be conditional")
            main.append((str(path), data, artifacts.read_frontmatter(path)[1]))
        elif re.fullmatch(rf"{re.escape(key)}-BRANCH-[A-Z]-E\d{{3}}", path.stem):
            if not data.get("branch") or f"-BRANCH-{data['branch']}-" not in path.stem or not data.get("gated_by"):
                raise WorkloadError(f"{path}: invalid conditional branch identity")
            branches.setdefault((data["branch"], data["gated_by"]), []).append(
                (str(path), data, artifacts.read_frontmatter(path)[1]))
        else:
            raise WorkloadError(f"{path}: unrecognized epic filename")

    by_id = {data["epic_id"]: data for _, data, _ in main}
    graph = {eid: data.get("dependencies", []) for eid, data in by_id.items()}
    for branch_key, entries in branches.items():
        gate = by_id.get(branch_key[1])
        if gate is None or gate["type"] != "Investigation":
            raise WorkloadError(f"{key}: conditional branch has no main Investigation gate")
        allowed = set(by_id) | {d["epic_id"] for _, d, _ in entries}
        for _, data, _ in entries:
            deps = data.get("dependencies", [])
            if any(dep not in allowed for dep in deps):
                raise WorkloadError(f"{key}: conditional dependency outside its branch")
            graph[data["epic_id"]] = deps
    for _, data, _ in main:
        dependencies = data.get("dependencies", [])
        if any(dep not in by_id for dep in dependencies):
            raise WorkloadError(f"{key}: unknown main epic dependency")
        gate_id = data.get("gated_by")
        if gate_id:
            gate = by_id.get(gate_id)
            if gate is None or gate["type"] != "Investigation" or gate_id not in dependencies:
                raise WorkloadError(f"{key}: main epic has invalid Investigation gate")
    try:
        tuple(TopologicalSorter(graph).static_order())
    except (CycleError, TypeError) as exc:
        raise WorkloadError(f"{key}: invalid dependency DAG") from exc

    components = selected.planning.canonical_components()
    for _, data, _ in main + [entry for entries in branches.values() for entry in entries]:
        selected.planning.validate_component(data["component"], components)
    plan = _build_plan(main, components, selected.submission)
    for entry in plan:
        identity = "epic-creator-id-" + _digest([selected.id, key, entry["epic_id"]])
        desired = dict(project=selected.submission.project, issue_type=selected.submission.issue_type,
            summary=entry["title"], description_adf=jira.markdown_to_adf(_build_description(entry["body"])),
            priority=entry["priority"], labels=entry["labels"],
            components=[entry["component"]] if entry["component"] else [],
            parent_key=key if selected.submission.parent["relation"] == "parent" else None)
        reviewed = {name: value for name, value in by_id[entry["epic_id"]].items() if name != "jira_key"}
        gated_branches = [({name: value for name, value in data.items() if name != "jira_key"}, body)
                          for (_, gate), items in sorted(branches.items()) if gate == entry["epic_id"]
                          for _, data, body in items]
        fingerprint = "epic-creator-plan-" + _digest({"fields": desired, "source": metadata,
            "artifact": reviewed, "body": entry["body"], "branches": gated_branches,
            "parent_policy": selected.submission.parent, "dependency_policy": selected.submission.dependency_link_type,
            "dependencies": entry["dependencies"]})
        desired["labels"] = desired["labels"] + [identity, fingerprint]
        entry.update(identity=identity, fingerprint=fingerprint, desired=desired)
    return {"key": key, "plan": plan, "branches": branches, "metadata": metadata,
            "source_body": artifacts.read_frontmatter(root / f"strat-tasks/{key}.md")[1].strip()}


def _save_journal(path, journal):
    """Replace the journal atomically and persist intent before a create request."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(journal, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _journal(root, selected, source, server):
    path = root / "submissions" / selected.id / f"{source}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkloadError(f"{source}: another submission holds the local lock") from exc
        binding = dict(schema_version=1, server=server.rstrip("/"), workload_id=selected.id, source_key=source)
        journal = json.loads(path.read_text()) if path.exists() else {**binding, "epics": {}}
        if (not isinstance(journal, dict) or any(journal.get(k) != v for k, v in binding.items())
                or not isinstance(journal.get("epics"), dict)):
            raise WorkloadError(f"{source}: invalid or conflicting submission journal")
        for record in journal["epics"].values():
            if (not isinstance(record, dict) or record.get("attempted") is not True
                    or not isinstance(record.get("identity"), str)
                    or not isinstance(record.get("fingerprint"), str)
                    or "jira_key" not in record):
                raise WorkloadError(f"{source}: invalid epic record in submission journal")
        yield path, journal


def _source_issue(selected, submission, credentials):
    key = submission["key"]
    issue = jira.get_issue(*credentials, key, fields=["project", "issuetype", "labels", "attachment", "assignee", "description"])
    selected.validate_ready(issue)
    metadata = submission["metadata"]
    attachment = _select_strategy_attachment(issue["fields"].get("attachment", []), key, selected)
    if not selected.description_fallback or metadata.get("attachment_id"):
        if (not attachment or attachment.get("id") != metadata.get("attachment_id")
                or attachment.get("created") != metadata.get("attachment_created")):
            raise WorkloadError(f"{key}: approved strategy attachment changed; fetch and review again")
    else:
        description = issue["fields"].get("description") or ""
        body = jira.adf_to_markdown(description) if isinstance(description, dict) else str(description)
        if attachment or body.strip() != submission["source_body"]:
            raise WorkloadError(f"{key}: approved strategy description changed; fetch and review again")
    return issue


def _verify_issue(selected, entry, issue, source):
    fields = issue.get("fields", {})
    if (not re.fullmatch(rf"{selected.submission.project}-\d+", issue.get("key", ""))
            or fields.get("project", {}).get("key") != selected.submission.project
            or fields.get("issuetype", {}).get("name") != selected.submission.issue_type
            or entry["identity"] not in fields.get("labels", [])
            or entry["fingerprint"] not in fields.get("labels", [])):
        raise WorkloadError(f"{entry['epic_id']}: conflicting Jira recovery identity/content")
    parent = (fields.get("parent") or {}).get("key")
    expected = source if selected.submission.parent["relation"] == "parent" else None
    if parent is not None and parent != expected:
        raise WorkloadError(f"{issue['key']}: conflicting Jira parent")


def _resolve(selected, entry, record, source, credentials):
    """An empty index result cannot clear a previously attempted create."""
    server, user, token = credentials
    result = jira.api_call(server, "/search/jql", user, token,
        body={"jql": f'labels = "{entry["identity"]}"', "fields": ["key"], "maxResults": 2}, method="POST")
    matches = result.get("issues")
    if (not isinstance(matches, list) or result.get("nextPageToken") or result.get("isLast") is not True
            or len(matches) > 1):
        raise WorkloadError(f"{entry['epic_id']}: duplicate or incomplete Jira recovery search")
    keys = {i["key"] for i in matches}
    if entry["jira_key"]:
        keys.add(entry["jira_key"])
    if record:
        if (not isinstance(record, dict) or record.get("identity") != entry["identity"]
                or record.get("fingerprint") != entry["fingerprint"]
                or record.get("attempted") is not True):
            raise WorkloadError(f"{entry['epic_id']}: journal conflicts with reviewed artifact")
        if record.get("jira_key"):
            keys.add(record["jira_key"])
    if len(keys) > 1:
        raise WorkloadError(f"{entry['epic_id']}: conflicting Jira keys; reconcile duplicates manually")
    if keys:
        key = keys.pop()
        issue = jira.get_issue(*credentials, key, fields=["project", "issuetype", "labels", "parent"])
        _verify_issue(selected, entry, issue, source)
        return key
    if record:
        raise WorkloadError(f"{entry['epic_id']}: prior create outcome unknown; wait for search or reconcile manually")
    return None


def _ensure_link(credentials, type_name, inward, outward):
    issue = jira.get_issue(*credentials, inward, fields=["issuelinks"])
    if any(link.get("type", {}).get("name") == type_name
           and link.get("outwardIssue", {}).get("key") == outward
           for link in issue["fields"].get("issuelinks", [])):
        return
    server, user, token = credentials
    jira.api_call(server, "/issueLink", user, token, body={"type": {"name": type_name},
        "inwardIssue": {"key": inward}, "outwardIssue": {"key": outward}}, method="POST")


def _ensure_attachment(credentials, key, stem, extension, content):
    digest = hashlib.sha256(content.encode()).hexdigest()
    filename = f"{key}-{stem}-{digest}.{extension}"
    issue = jira.get_issue(*credentials, key, fields=["attachment"])
    matches = [a for a in issue["fields"].get("attachment", []) if a["filename"] == filename]
    if len(matches) > 1:
        raise WorkloadError(f"{key}: duplicate evidence attachments; reconcile manually")
    if not matches:
        jira.add_attachment(*credentials, key, filename, content, retry=False)


def _execute(selected, submission, root, credentials):
    key, plan = submission["key"], submission["plan"]
    with _journal(root, selected, key, credentials[0]) as (path, journal):
        source = _source_issue(selected, submission, credentials)
        if set(journal["epics"]) - {e["epic_id"] for e in plan}:
            raise WorkloadError(f"{key}: journal contains epics absent from reviewed plan")
        # Resolve the entire set before making the first remote write.
        resolved = {e["epic_id"]: _resolve(selected, e, journal["epics"].get(e["epic_id"]), key, credentials)
                    for e in plan}
        assignee = (source["fields"].get("assignee") or {}).get("accountId")
        created = 0
        for entry in plan:
            eid = entry["epic_id"]
            record = dict(identity=entry["identity"], fingerprint=entry["fingerprint"],
                          attempted=True, jira_key=resolved[eid])
            journal["epics"][eid] = record
            _save_journal(path, journal)
            if not record["jira_key"]:
                record["jira_key"] = jira.create_issue(*credentials, **entry["desired"], assignee_id=assignee, retry=False)
                if not re.fullmatch(rf"{selected.submission.project}-\d+", record["jira_key"]):
                    raise WorkloadError(f"{eid}: Jira returned an invalid created key")
                created += 1
                _save_journal(path, journal)
            resolved[eid] = record["jira_key"]
            if entry["jira_key"] != record["jira_key"]:
                artifacts.update_frontmatter(entry["path"], {"jira_key": record["jira_key"]}, "epic-task")

        server, user, token = credentials
        for entry in plan:
            target = resolved[entry["epic_id"]]
            issue = jira.get_issue(*credentials, target, fields=["labels", "parent"])
            body = {}
            missing_labels = [label for label in entry["desired"]["labels"] if label not in issue["fields"].get("labels", [])]
            if missing_labels:
                body["update"] = {"labels": [{"add": label} for label in missing_labels]}
            if selected.submission.parent["relation"] == "parent":
                if not issue["fields"].get("parent"):
                    body["fields"] = {"parent": {"key": key}}
            else:
                _ensure_link(credentials, selected.submission.parent["link_type"], key, target)
            if body:
                jira.api_call(server, f"/issue/{target}", user, token, body=body, method="PUT")
            for dependency in entry["dependencies"]:
                _ensure_link(credentials, selected.submission.dependency_link_type, resolved[dependency], target)

        for entry in plan:
            data, _ = artifacts.read_frontmatter(entry["path"])
            content = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
            _ensure_attachment(credentials, resolved[entry["epic_id"]], "frontmatter", "yaml", content)
        from submit import _build_branch_plan_md
        for (branch, gate), entries in sorted(submission["branches"].items()):
            _ensure_attachment(credentials, resolved[gate], f"branch-{branch.lower()}-plan", "md",
                               _build_branch_plan_md(entries))

        issue = jira.get_issue(*credentials, key, fields=["labels"])
        if selected.processed_label not in issue["fields"].get("labels", []):
            jira.api_call(server, f"/issue/{key}", user, token,
                body={"update": {"labels": [{"add": selected.processed_label}]}}, method="PUT")
        print(f"{key}: submission complete ({created} created, {len(plan) - created} recovered/existing)")


def run_submission(args):
    """CLI entry point. Preflight all selected local plans before any Jira writes."""
    try:
        selected = workload.get_workload(args.workload)
        if selected.planning is None or selected.submission is None:
            raise WorkloadError(f"{selected.id}: planning and submission policies are required")
        if selected.submission.project != selected.planning.component_project:
            raise WorkloadError(f"{selected.id}: submission and component projects must match")
        root = Path(args.artifacts_dir)
        keys = args.strat_ids
        if args.all:
            keys = [p.name.removesuffix("-decomposition.md") for p in sorted((root / "epic-tasks").glob("*-decomposition.md"))
                    if re.fullmatch(selected.id_pattern, p.name.removesuffix("-decomposition.md"))]
        if not keys:
            raise WorkloadError("No decompositions for selected workload")
        submissions = [preflight(selected, key, root) for key in dict.fromkeys(keys)]
        if args.dry_run:
            for submission in submissions:
                print(f"[DRY RUN] {submission['key']}: {selected.submission.project}/{selected.submission.issue_type}")
                for entry in submission["plan"]:
                    print(f"  {entry['epic_id']}: {entry['title']} (identity {entry['identity']})")
                    print(f"    parent: {submission['key']} via {selected.submission.parent}")
                    print(f"    {selected.submission.dependency_link_type} dependencies: {entry['dependencies']}")
                    print(f"    components: {entry['desired']['components']}; labels: {entry['desired']['labels']}")
            return 0
        credentials = jira.require_env()
        if not all(credentials):
            raise WorkloadError("JIRA_SERVER, JIRA_USER and JIRA_TOKEN are required")
        for submission in submissions:
            _execute(selected, submission, root, credentials)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
