"""Repository preflight and retry recovery for the existing Jira publisher.

Jira labels are searchable identities, not uniqueness constraints. Callers must
serialize independent workspaces and retain artifacts/submissions/ across retries.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error

import yaml

import artifact_utils as artifacts
import jira_utils as jira
from pipeline_repositories import source_metadata, validate_artifact, validate_decomposition, repository_plan_for
import rhai_config
from rhai_config import RepositoryError


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def preflight(selected, key, root):
    """Validate every local artifact, including conditional branches, before writes."""
    from submit import _build_plan, _build_description, TARGET_PROJECT, TARGET_ISSUE_TYPE, DEPENDENCY_LINK_TYPE

    state = {"rhai_config_digest": selected.digest, "planning_digest": selected.planning_digest}
    if not selected.repository_policy:
        raise RepositoryError("RHAI repository mode is disabled")
    metadata = source_metadata(state, key, root)
    validate_decomposition(state, key, root)
    review = validate_artifact(state, key, root / f"epic-reviews/{key}-decomp-review.md",
                               "decomp-review", root)
    if not review["pass"]:
        raise RepositoryError(f"{key}: review did not pass")

    main, branches = [], {}
    for path in sorted((root / "epic-tasks").glob(f"{key}-*.md")):
        if path.name == f"{key}-decomposition.md" or path.name.endswith("-ai-signals.md"):
            continue
        # validate_decomposition already checks these artifacts against the plan.
        data, body = artifacts.read_frontmatter(path)
        if re.fullmatch(rf"{re.escape(key)}-E\d{{3}}", path.stem):
            if data.get("branch"):
                raise RepositoryError(f"{path}: main epic cannot be conditional")
            main.append((str(path), data, body))
        elif re.fullmatch(rf"{re.escape(key)}-BRANCH-[A-Z]-E\d{{3}}", path.stem):
            if not data.get("branch") or f"-BRANCH-{data['branch']}-" not in path.stem or not data.get("gated_by"):
                raise RepositoryError(f"{path}: invalid conditional branch identity")
            branches.setdefault((data["branch"], data["gated_by"]), []).append(
                (str(path), data, body))
        else:
            raise RepositoryError(f"{path}: unrecognized epic filename")

    by_id = {data["epic_id"]: data for _, data, _ in main}
    components = selected.canonical_components()
    repository_context = repository_plan_for(state, key, root, prepare=True)
    repository_plan = repository_plan_for(state, key, root)
    plan = _build_plan(main, components, repository_context["inventory"])
    for entry in plan:
        description = _build_description(entry["body"])
        data = by_id[entry["epic_id"]]
        scope = next(scope for scope in repository_plan["scopes"] if scope["scope_id"] == data["repository_scope"])
        entry["repository_warning"] = scope.get("repository_warning")
        target = data.get("target_repository")
        if target:
            url = f"https://{target['host']}/{target['namespace']}/{target['repository']}"
            description += f"\n\n## Target repository\n\n{url}\n\nInventory key: {target['inventory_key']}\n"
        description += f"\nMapping snapshot: {repository_context['digest']}\n"
        entry["repository_evidence"] = {
            "mapping_snapshot_digest": repository_context["digest"],
            "repository_plan_digest": repository_plan["digest"],
            "inventory_revision": repository_context["inventory_revision"],
            "scope": scope,
            "work_items": [item for item in repository_plan["work_items"] if item["id"] in scope["work_item_ids"]],
            "sources": [{**{k: v for k, v in source.items() if k not in ("files", "fetched_at")},
                         "files": {name: {"sha256": file["sha256"]} for name, file in source["files"].items()}}
                        for source in repository_context["sources"]],
            "inventory_entry": repository_context["inventory"].get(target["inventory_key"]) if target else None}
        if repository_context.get("guidance"):
            entry["repository_evidence"]["guidance"] = {
                key: repository_context["guidance"][key] for key in ("source_id", "path", "sha256")}
        identity = "epic-creator-id-" + _digest(["rhai", key, entry["epic_id"]])
        desired = dict(project=TARGET_PROJECT, issue_type=TARGET_ISSUE_TYPE,
            summary=entry["title"], description_adf=jira.markdown_to_adf(description),
            priority=entry["priority"], labels=entry["labels"],
            components=entry.get("components", [entry["component"]] if entry["component"] else []),
            parent_key=key, repo_url=entry["repo_url"])
        reviewed = {name: value for name, value in by_id[entry["epic_id"]].items() if name != "jira_key"}
        gated_branches = [({name: value for name, value in data.items() if name != "jira_key"}, body)
                          for (_, gate), items in sorted(branches.items()) if gate == entry["epic_id"]
                          for _, data, body in items]
        fingerprint = "epic-creator-plan-" + _digest({"fields": desired, "source": metadata,
            "artifact": reviewed, "body": entry["body"], "branches": gated_branches,
            "parent_policy": {"relation": "parent"}, "dependency_policy": DEPENDENCY_LINK_TYPE,
            "dependencies": entry["dependencies"]})
        desired["labels"] = desired["labels"] + [identity, fingerprint]
        entry["labels"] = desired["labels"]
        entry.update(identity=identity, fingerprint=fingerprint, description_adf=desired["description_adf"], desired=desired)
    return {"key": key, "plan": plan, "branches": branches, "metadata": metadata,
            "unresolved": repository_plan["unresolved"],
            "repository_context": repository_context,
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
def _journal(root, source, server):
    path = root / "submissions" / "rhai" / f"{source}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RepositoryError(f"{source}: another submission holds the local lock") from exc
        binding = dict(schema_version=1, server=server.rstrip("/"), rhai_config_id="rhai", source_key=source)
        journal = json.loads(path.read_text()) if path.exists() else {**binding, "epics": {}}
        if (not isinstance(journal, dict) or any(journal.get(k) != v for k, v in binding.items())
                or not isinstance(journal.get("epics"), dict)):
            raise RepositoryError(f"{source}: invalid or conflicting submission journal")
        for record in journal["epics"].values():
            if (not isinstance(record, dict) or record.get("attempted") is not True
                    or not isinstance(record.get("identity"), str)
                    or not isinstance(record.get("fingerprint"), str)
                    or "jira_key" not in record):
                raise RepositoryError(f"{source}: invalid epic record in submission journal")
        yield path, journal


def _source_issue(selected, submission, credentials):
    key = submission["key"]
    issue = jira.get_issue(*credentials, key, fields=["project", "issuetype", "labels", "attachment", "assignee", "description"])
    selected.validate_ready(issue)
    metadata = submission["metadata"]
    candidates = [a for a in issue["fields"].get("attachment", [])
                  if a.get("filename") == f"{key}-strategy.md"]
    attachment = max(candidates, key=lambda a: a.get("created", "")) if candidates else None
    if metadata.get("attachment_id"):
        if (not attachment or str(attachment.get("id")) != metadata["attachment_id"]
                or attachment.get("created") != metadata.get("attachment_created")):
            raise RepositoryError(f"{key}: approved strategy attachment changed; fetch and review again")
        body = jira.download_attachment(attachment["content"], credentials[1], credentials[2],
                                        server=credentials[0]).decode("utf-8").strip()
    else:
        description = issue["fields"].get("description") or ""
        body = jira.adf_to_markdown(description) if isinstance(description, dict) else str(description)
        if attachment:
            raise RepositoryError(f"{key}: approved strategy attachment changed; fetch and review again")
    if (body.strip() != submission["source_body"] or
            hashlib.sha256(body.strip().encode()).hexdigest() != metadata["source_revision"]):
        raise RepositoryError(f"{key}: approved strategy content changed; fetch and review again")
    return issue


def _normalize_adf(value):
    """Jira may add empty node attributes when storing an ADF description."""
    if isinstance(value, list):
        return [_normalize_adf(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_adf(item) for key, item in value.items()
                if not (key == "attrs" and item == {})}
    return value


def _verify_issue(entry, issue, source):
    from submit import TARGET_PROJECT, TARGET_ISSUE_TYPE
    fields = issue.get("fields", {})
    if (not re.fullmatch(rf"{TARGET_PROJECT}-\d+", issue.get("key", ""))
            or fields.get("project", {}).get("key") != TARGET_PROJECT
            or fields.get("issuetype", {}).get("name") != TARGET_ISSUE_TYPE
            or entry["identity"] not in fields.get("labels", [])
            or entry["fingerprint"] not in fields.get("labels", [])):
        raise RepositoryError(f"{entry['epic_id']}: conflicting Jira recovery identity/content")
    parent = (fields.get("parent") or {}).get("key")
    if parent != source:
        raise RepositoryError(f"{issue['key']}: conflicting Jira parent")
    if "repo_url" in entry:
        if (fields.get(entry["repo_url_field"]) or None) != entry["repo_url"]:
            raise RepositoryError(f"{issue['key']}: conflicting or missing repository URL (customfield_10933); reconcile explicitly")
        desired = entry["desired"]
        if (fields.get("summary") != desired["summary"]
                or _normalize_adf(fields.get("description")) != _normalize_adf(desired["description_adf"])
                or sorted(c["name"] for c in fields.get("components", [])) != sorted(desired["components"])):
            raise RepositoryError(f"{issue['key']}: published repository scope differs from reviewed plan")


def _resolve(entry, record, source, credentials):
    """An empty index result cannot clear a previously attempted create."""
    server, user, token = credentials
    result = jira.api_call(server, "/search/jql", user, token,
        body={"jql": f'labels = "{entry["identity"]}"', "fields": ["key"], "maxResults": 2}, method="POST")
    matches = result.get("issues")
    if (not isinstance(matches, list) or result.get("nextPageToken") or result.get("isLast") is not True
            or len(matches) > 1):
        raise RepositoryError(f"{entry['epic_id']}: duplicate or incomplete Jira recovery search")
    keys = {i["key"] for i in matches}
    if entry["jira_key"]:
        keys.add(entry["jira_key"])
    if record:
        if (not isinstance(record, dict) or record.get("identity") != entry["identity"]
                or record.get("fingerprint") != entry["fingerprint"]
                or record.get("attempted") is not True):
            raise RepositoryError(f"{entry['epic_id']}: journal conflicts with reviewed artifact")
        if record.get("jira_key"):
            keys.add(record["jira_key"])
    if len(keys) > 1:
        raise RepositoryError(f"{entry['epic_id']}: conflicting Jira keys; reconcile duplicates manually")
    if keys:
        key = keys.pop()
        issue = jira.get_issue(*credentials, key, fields=["project", "issuetype", "labels", "parent", "summary", "description", "components", entry["repo_url_field"]])
        _verify_issue(entry, issue, source)
        return key
    if record:
        raise RepositoryError(f"{entry['epic_id']}: prior create outcome unknown; wait for search or reconcile manually")
    return None


def _repository_url_field(credentials):
    """Require the existing URL field customfield_10933 on the RHAI Epic create screen."""
    from submit import TARGET_PROJECT, TARGET_ISSUE_TYPE

    def pages(path, key):
        values, start = [], 0
        while True:
            server, user, token = credentials
            result = jira.api_call_with_retry(server,
                f"{path}?startAt={start}&maxResults=100", user, token)
            batch = result.get(key)
            if not isinstance(batch, list):
                raise RepositoryError("Jira returned invalid create-field metadata")
            values.extend(batch)
            start += len(batch)
            if result.get("isLast") is True or ("total" in result and start >= result["total"]):
                return values
            if not batch:
                raise RepositoryError("Jira returned incomplete create-field metadata")

    types = [item for item in pages(f"/issue/createmeta/{TARGET_PROJECT}/issuetypes", "issueTypes")
             if item.get("name") == TARGET_ISSUE_TYPE]
    if len(types) != 1:
        raise RepositoryError("Cannot uniquely resolve the RHAI Epic issue type")
    fields = pages(f"/issue/createmeta/{TARGET_PROJECT}/issuetypes/{types[0]['id']}", "fields")
    matches = [field for field in fields if field.get("fieldId", field.get("key")) == "customfield_10933"]
    if len(matches) != 1:
        raise RepositoryError("RHAI Epics require URL field customfield_10933 on the create screen")
    field = matches[0]
    field_id = field.get("fieldId", field.get("key", ""))
    schema = field.get("schema", {})
    if (field_id != "customfield_10933" or schema.get("type") != "string"
            or schema.get("custom") != "com.atlassian.jira.plugin.system.customfieldtypes:url"):
        raise RepositoryError("customfield_10933 (URL) must be a Jira URL custom field")
    return field_id


@contextmanager
def publication(submission, root, credentials):
    """Check the source and recover keys before submit.py runs its usual phases."""
    selected = rhai_config.load_config()
    key, plan = submission["key"], submission["plan"]
    from repository_context import assert_context_current
    assert_context_current(submission["repository_context"], selected, root / f"strat-tasks/{key}.md")
    # Resolve before any mutation, including unresolved/Investigation-only batches.
    field_id = _repository_url_field(credentials)
    for entry in plan:
        entry["repo_url_field"] = field_id
        entry["custom_fields"] = {field_id: entry["repo_url"]} if entry["repo_url"] else {}
    with _journal(root, key, credentials[0]) as (path, journal):
        source = _source_issue(selected, submission, credentials)
        if set(journal["epics"]) - {entry["epic_id"] for entry in plan}:
            raise RepositoryError(f"{key}: journal contains epics absent from reviewed plan")
        resolved = {entry["epic_id"]: _resolve(entry, journal["epics"].get(entry["epic_id"]), key, credentials)
                    for entry in plan}

        def warn(entry, jira_key):
            warning = entry.get("repository_warning")
            if not warning:
                return
            body = jira.text_to_adf_paragraph(warning)
            if not any(comment.get("body") == body for comment in jira.get_comments(*credentials, jira_key)):
                jira.api_call(credentials[0], f"/issue/{jira_key}/comment", credentials[1], credentials[2],
                              body={"body": body}, method="POST")

        for entry in plan:
            recovered = resolved[entry["epic_id"]]
            if recovered:
                warn(entry, recovered)
            if recovered and recovered != entry["jira_key"]:
                artifacts.update_frontmatter(entry["path"], {"jira_key": recovered}, "epic-task")
                entry["jira_key"] = recovered

        def create(entry, **fields):
            eid = entry["epic_id"]
            record = dict(identity=entry["identity"], fingerprint=entry["fingerprint"], attempted=True, jira_key=None)
            journal["epics"][eid] = record
            _save_journal(path, journal)
            try:
                record["jira_key"] = jira.create_issue(*credentials, **fields, retry=False)
            except urllib.error.HTTPError as exc:
                if 400 <= exc.code < 500:
                    journal["epics"].pop(eid, None)
                    _save_journal(path, journal)
                raise
            if not re.fullmatch(r"RHAI-\d+", record["jira_key"]):
                raise RepositoryError(f"{eid}: Jira returned an invalid created key")
            _save_journal(path, journal)
            issue = jira.get_issue(*credentials, record["jira_key"],
                fields=["project", "issuetype", "labels", "parent", "summary", "description", "components", entry["repo_url_field"]])
            _verify_issue(entry, issue, key)
            warn(entry, record["jira_key"])
            return record["jira_key"]

        yield create, (source["fields"].get("assignee") or {}).get("accountId")
