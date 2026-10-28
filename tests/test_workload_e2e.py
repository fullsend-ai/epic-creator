"""CLI-to-HTTPS verification; all Jira traffic stays on a local test server.

Run with pytest. Set WORKLOAD_E2E_MODEL=1 to additionally run the tool-free
Claude planning adapter. No production Jira credentials are used or inherited.
"""
from copy import deepcopy
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
APPROVAL = ["strat-creator-rubric-pass", "strat-creator-human-sign-off"]
STRATEGY = """## Problem Statement
Dashboard users need a clear warning when their project cache is incompatible.

## High-Level Requirements
- HLR-1 [P1]: Determine whether the existing Dashboard cache format supports
  the warning; the result selects a compatible-format or migration branch.
- HLR-2 [P1]: Show the cache warning in Dashboard, with an accessible message
  and a test covering compatible and incompatible projects.

## Affected Components
- Dashboard, owned by the Dashboard team, already in the platform.

## Open Questions
Does the existing cache format support the warning without a migration?
This determines the warning implementation's approach and the branch selected.

## Out of Scope
New services, unrelated UI changes, infrastructure changes and Jira writes.
"""


class LocalJira:
    """A wire-level Jira substitute, including ambiguous transport outcomes."""

    def __init__(self):
        self.issues = {}
        self.contents = {}
        self.requests = []
        self.links = set()
        self.drop_after = None
        self.next_key = 1

    @property
    def writes(self):
        return [r for r in self.requests
                if r["method"] != "GET" and r["path"] != "/rest/api/3/search/jql"]

    def handle(self, method, url, headers, raw):
        parsed = urlsplit(url)
        path = parsed.path.removeprefix("/rest/api/3")
        body = json.loads(raw) if raw and "application/json" in headers.get("Content-Type", "") else None
        self.requests.append({"method": method, "path": parsed.path, "body": deepcopy(body)})
        if method == "GET" and path.startswith("/attachment/"):
            return self.contents[path.rsplit("/", 1)[-1]].encode()
        if method == "GET" and path.startswith("/project/"):
            return [{"name": "Dashboard"}]
        if path == "/search/jql":
            query = body or {k: v[0] for k, v in parse_qs(parsed.query).items()}
            marker = re.search(r'labels = "([^"]+)"', query["jql"])
            matches = [i for i in self.issues.values()
                       if (marker and marker[1] in i["fields"].get("labels", []))
                       or (not marker and i["fields"]["issuetype"]["name"] != "Epic")]
            return {"issues": deepcopy(matches), "isLast": True}
        if method == "GET" and path.startswith("/issue/"):
            return deepcopy(self.issues[path.split("/")[2]])
        if method == "POST" and path == "/issue":
            fields = body["fields"]
            fields["issuetype"]["name"] = fields["issuetype"].pop("name", None) or "Epic"
            key = f"{fields['project']['key']}-{self.next_key}"
            self.next_key += 1
            fields.update(attachment=[], issuelinks=[])
            self.issues[key] = {"key": key, "id": str(self.next_key), "fields": fields}
            return {"key": key, "id": str(self.next_key)}
        if method == "PUT" and path.startswith("/issue/"):
            fields = self.issues[path.split("/")[2]]["fields"]
            fields.update(body.get("fields", {}))
            for change in body.get("update", {}).get("labels", []):
                if change["add"] not in fields["labels"]:
                    fields["labels"].append(change["add"])
            return None
        if method == "POST" and path == "/issueLink":
            inward, outward = body["inwardIssue"]["key"], body["outwardIssue"]["key"]
            name = body["type"]["name"]
            self.links.add((name, inward, outward))
            self.issues[inward]["fields"]["issuelinks"].append(
                {"type": {"name": name}, "outwardIssue": {"key": outward}})
            self.issues[outward]["fields"]["issuelinks"].append(
                {"type": {"name": name}, "inwardIssue": {"key": inward}})
            return None
        if method == "POST" and path.endswith("/attachments"):
            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {headers['Content-Type']}\r\n\r\n".encode() + raw)
            part = next(message.iter_parts())
            key = path.split("/")[2]
            attachment = {"id": str(len(self.requests)), "filename": part.get_filename()}
            self.issues[key]["fields"]["attachment"].append(attachment)
            return [attachment]
        raise AssertionError(f"Unexpected request: {method} {path}")


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    directory = tmp_path_factory.mktemp("e2e-tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                   capture_output=True, text=True, check=True)
    return cert, key


@pytest.fixture
def local_jira(certificate, tmp_path):
    jira = LocalJira()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                result = jira.handle(self.command, self.path, self.headers, raw)
            except Exception as exc:
                self.send_error(400, str(exc))
                return
            operation = (self.command, urlsplit(self.path).path.removeprefix("/rest/api/3"))
            if jira.drop_after == operation:
                jira.drop_after = None
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            data = result if isinstance(result, bytes) else json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*certificate)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    jira.url = f"https://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield jira
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    (tmp_path / "http-requests.json").write_text(json.dumps(jira.requests, indent=2))


class Workspace:
    def __init__(self, path, jira, certificate, workload_id="rhai-initiative"):
        self.path, self.jira, self.workload_id = path, jira, workload_id
        self.commands = []
        for name in ("scripts", "skills", "workloads"):
            shutil.copytree(ROOT / name, path / name)
        if workload_id == "example-initiative":
            descriptor = yaml.safe_load((path / "workloads/rhai-initiative.yaml").read_text())
            descriptor["id"] = workload_id
            descriptor["source"].update(project="EXAMPLE", id_pattern=r"^EXAMPLE-\d+$")
            descriptor["planning"]["components"]["project"] = "OTHER"
            descriptor["planning"]["review_gate"]["pass_score"] = 11
            descriptor["submission"].update(project="OTHER", dependency_link_type="Requires",
                                           parent={"relation": "link", "link_type": "Relates"})
            descriptor["submission"]["labels"]["created"] = "example-created"
            descriptor["planning"]["decompose"] = "workloads/policies/example/decompose.md"
            policy = path / descriptor["planning"]["decompose"]
            policy.parent.mkdir(parents=True)
            policy.write_text("# Example policy selected\n" +
                              (path / "workloads/policies/rhai/v1/decompose.md").read_text())
            (path / f"workloads/{workload_id}.yaml").write_text(yaml.safe_dump(descriptor))
        self.descriptor = yaml.safe_load((path / f"workloads/{workload_id}.yaml").read_text())
        source = self.descriptor["source"]
        self.key = source["project"] + "-9001"
        attachment = dict(id="2", filename=f"{self.key}-strategy.md",
                          created="2026-10-18T01:00:00Z", content=f"{jira.url}/attachment/2")
        old = dict(attachment, id="1", created="2026-10-17T01:00:00Z", content=f"{jira.url}/attachment/1")
        jira.contents.update({"1": "Old unapproved strategy body", "2": STRATEGY})
        jira.issues[self.key] = {"key": self.key, "id": "9001", "fields": {
            "summary": "Synthetic Dashboard cache warning", "description": "Initiative description is not its strategy",
            "project": {"key": source["project"]}, "issuetype": {"name": source["issue_type"]},
            "status": {"name": "Approved"}, "priority": {"name": "Major"}, "updated": "2026-10-18T02:00:00Z",
            "labels": list(APPROVAL), "assignee": {"accountId": "synthetic-account"},
            "attachment": [old, attachment], "issuelinks": []}}
        # Explicit environment: production Jira credentials and proxies cannot leak in.
        self.env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8",
                    "PYTHONUNBUFFERED": "1", "SSL_CERT_FILE": str(certificate[0]),
                    "JIRA_SERVER": jira.url, "JIRA_USER": "synthetic", "JIRA_TOKEN": "synthetic"}
        guard = path / "network-guard"
        guard.mkdir()
        (guard / "sitecustomize.py").write_text(
            "import sys\n"
            "def guard(event, args):\n"
            f"    if event == 'socket.connect' and args[1] != ('127.0.0.1', {urlsplit(jira.url).port}):\n"
            "        raise RuntimeError('E2E blocked non-test network destination')\n"
            "sys.addaudithook(guard)\n")
        self.env["PYTHONPATH"] = str(guard)

    def cli(self, script, *args, expected=0, env=None):
        command = [sys.executable, f"scripts/{script}.py", *map(str, args)]
        result = subprocess.run(command, cwd=self.path, env=self.env if env is None else env,
                                capture_output=True, text=True, timeout=120)
        self.commands.append(dict(command=command[1:], returncode=result.returncode,
                                  stdout=result.stdout, stderr=result.stderr))
        (self.path / "commands.json").write_text(json.dumps(self.commands, indent=2))
        if expected is not None:
            assert result.returncode == expected, f"{command}\n{result.stdout}\n{result.stderr}"
        return result

    def metadata(self, relative):
        return yaml.safe_load((self.path / relative).read_text().split("---", 2)[1])

    def write(self, relative, body=None, **fields):
        target = self.path / relative
        if body is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_text().startswith("---"):
                previous = self.metadata(relative)
                target.write_text("---\n" + yaml.safe_dump(previous) + "---\n" + body)
            else:
                target.write_text(body)
        arguments = [f"{name}={value if isinstance(value, str) else json.dumps(value)}"
                     for name, value in fields.items()]
        if arguments:
            self.cli("frontmatter", "set", "--source", f"artifacts/strat-tasks/{self.key}.md",
                     relative, *arguments)

    def deterministic_worker(self, phase, agent, *, rationales=True, main_gate=True):
        variables = dict(line.split("=", 1) for line in agent["vars"].splitlines())
        assert variables["WORKLOAD_ID"] == self.workload_id
        assert variables["ID"] == self.key
        if phase == "FETCH":
            self.cli("fetch_strategy", "fetch-one", self.key, "--workload", self.workload_id)
            return
        policy = Path(variables["POLICY_FILE"])
        assert policy.is_relative_to(self.path)
        assert policy.is_file()
        if self.workload_id == "example-initiative" and phase == "DECOMPOSE":
            assert "Example policy selected" in policy.read_text()
        if phase == "DECOMPOSE":
            for number in (1, 2, 3):
                branch = number == 3
                eid = f"{self.key}-{'BRANCH-A-' if branch else ''}E{number:03}"
                fields = dict(epic_id=eid, parent_strat=self.key, title=f"Cache warning work {number}",
                              component="Dashboard", team="Dashboard", priority="P1",
                              type="Investigation" if number == 1 else "Implementation",
                              dependencies=[] if number == 1 else [f"{self.key}-E001"])
                if number == 1:
                    fields["investigation_signals"] = dict(question_specificity=1, source_accessibility=1,
                                                           local_runnability=1)
                else:
                    fields["ai_signals"] = dict(change_specificity=1, pattern_precedent=1, existing_foundation=1)
                if branch or (main_gate and number == 2):
                    fields.update(gated_by=f"{self.key}-E001",
                                  gate_failure_impact={"action": "rewrite", "fallback_approach": "Adapt the warning to the migration result"})
                if branch:
                    fields["branch"] = "A"
                self.write(f"artifacts/epic-tasks/{eid}.md", body="## Description\nCache warning work.\n\n## Acceptance Criteria\nWarning is tested.\n", **fields)
                if rationales:
                    (self.path / f"artifacts/epic-tasks/{eid}-ai-signals.md").write_text(
                        "# AI signal rationales\nThe strategy names the component, required output and validation.\n")
            self.write(f"artifacts/epic-tasks/{self.key}-decomposition.md", body="## Epic List\nTwo main epics and a conditional plan.\n",
                       parent_strat=self.key, epic_count=2, critical_path_length=2, decompose_complete=True)
        elif phase in ("REVIEW_DECOMP", "RE_REVIEW"):
            scores = [0, 2, 2, 2, 2, 2, 2] if phase == "REVIEW_DECOMP" else [2] * 7
            passed = phase == "RE_REVIEW"
            self.write(f"artifacts/epic-reviews/{self.key}-decomp-review.md", body="## Review Summary\nCheck HLR-2 warning coverage.\n",
                       strat_id=self.key, score=sum(scores), criterion_scores=scores,
                       recommendation="accept" if passed else "revise", **{"pass": passed},
                       issues=[] if passed else [{"severity": "major", "criterion": "HLR Coverage", "description": "Add the incompatible-project warning check"}])
            if not passed:
                refused = self.submit("--dry-run", expected=None)
                assert refused.returncode != 0 and "review did not pass" in refused.stderr
                assert not self.jira.writes
        elif phase == "REVISE_DECOMP":
            self.write(f"artifacts/epic-tasks/{self.key}-E002.md",
                       body="## Description\nCache warning.\n\n## Acceptance Criteria\nCompatible and incompatible project warnings are tested (HLR-2).\n")
            self.write(f"artifacts/epic-tasks/{self.key}-decomposition.md", revised=True)
        else:
            raise AssertionError(f"Unexpected worker phase: {phase}")

    def pipeline(self, worker=None, **options):
        self.cli("fetch_components", "--workload", self.workload_id)
        self.cli("pipeline_state", "init", "--headless", "--workload", self.workload_id)
        for name in ("all", "batch-1"):
            self.cli("state", "write-ids", f"tmp/pipeline-{name}-ids.txt", self.key)
        self.cli("pipeline_state", "set", "total_batches=1")
        self.cli("pipeline_state", "set-phase", "BATCH_START")
        phases = []
        for _ in range(30):
            action = yaml.safe_load(self.cli("pipeline_state", "next-action").stdout)
            if action["action"] == "done":
                break
            phase = action["phase"]
            phases.append(phase)
            if action["action"] == "run_script":
                self.cli("pipeline_state", "run-phase")
            else:
                for agent in action["agents"]:
                    (worker or self.deterministic_worker)(phase, agent, **options)
                self.cli("pipeline_state", "wait-for-wave")
        else:
            pytest.fail("Pipeline failed to terminate")
        assert self.metadata(f"artifacts/strat-tasks/{self.key}.md")["attachment_id"] == "2"
        report = yaml.safe_load(next((self.path / "artifacts/decompose-runs").glob("*.yaml")).read_text())
        assert report["workload_id"] == self.workload_id
        assert report["results"][0]["status"] == "passed"
        assert yaml.safe_load((self.path / "tmp/pipeline-state.yaml").read_text())["phase"] == "DONE"
        return phases

    def submit(self, *args, expected=0, env=None):
        return self.cli("submit", "--workload", self.workload_id, *args, self.key,
                        expected=expected, env=env)


@pytest.fixture
def workspace(tmp_path, local_jira, certificate):
    return Workspace(tmp_path, local_jira, certificate)


@pytest.mark.parametrize("workload_id,rationales,main_gate", [
    ("rhai-initiative", True, False),
    ("rhai-initiative", False, True),
    ("rhaistrat-strategy", True, True),
    ("example-initiative", True, True),
])
def test_cli_flow_accepts_prompt_artifacts(tmp_path, local_jira, certificate, workload_id, rationales, main_gate):
    w = Workspace(tmp_path, local_jira, certificate, workload_id)
    assert w.pipeline(rationales=rationales, main_gate=main_gate) == [
        "FETCH", "DECOMPOSE", "REVIEW_DECOMP", "REVISE_DECOMP", "RE_REVIEW", "REPORT"]
    before = len(local_jira.requests)
    env = {k: v for k, v in w.env.items() if not k.startswith("JIRA_")}
    assert "[DRY RUN]" in w.submit("--dry-run", env=env).stdout
    assert len(local_jira.requests) == before
    w.submit()
    created = [i for key, i in local_jira.issues.items() if key != w.key]
    assert len(created) == 2  # Conditional branch is evidence, not a live Epic.
    policy = w.descriptor["submission"]
    for issue in created:
        fields = issue["fields"]
        assert fields["project"]["key"] == policy["project"]
        assert fields["issuetype"]["name"] == policy["issue_type"]
        assert fields["assignee"]["accountId"] == "synthetic-account"
        assert fields["components"] == [{"name": "Dashboard"}]
        assert policy["labels"]["created"] in fields["labels"]
        assert any(label.startswith("epic-creator-id-") for label in fields["labels"])
        if policy["parent"]["relation"] == "parent":
            assert fields["parent"] == {"key": w.key}
        else:
            assert ("Relates", w.key, issue["key"]) in local_jira.links
    assert (policy["dependency_link_type"], f"{policy['project']}-1", f"{policy['project']}-2") in local_jira.links
    second = local_jira.issues[f"{policy['project']}-2"]["fields"]
    assert "Compatible and incompatible project warnings are tested (HLR-2)." in json.dumps(second["description"])
    first = local_jira.issues[f"{policy['project']}-1"]["fields"]
    assert any("branch-a-plan" in a["filename"] for a in first["attachment"])
    assert local_jira.writes[-1]["path"] == f"/rest/api/3/issue/{w.key}"
    writes = deepcopy(local_jira.writes)
    w.submit()
    assert local_jira.writes == writes


def test_network_guard_blocks_non_test_destination(workspace):
    p = subprocess.run([sys.executable, "-c", "import socket; socket.socket().connect(('192.0.2.1',443))"],
                       env=workspace.env, capture_output=True, text=True, timeout=5)
    assert p.returncode != 0
    assert "E2E blocked non-test network destination" in p.stderr
    assert not workspace.jira.requests


@pytest.mark.parametrize("change", ["approval", "attachment", "provenance", "policy", "review"])
def test_cli_refuses_stale_or_unapproved_plan_before_writes(workspace, change):
    w = workspace
    w.pipeline(rationales=False, main_gate=False)
    if change == "approval":
        w.jira.issues[w.key]["fields"]["labels"].remove(APPROVAL[0])
    elif change == "attachment":
        w.jira.issues[w.key]["fields"]["attachment"][-1]["created"] = "2026-10-18T03:00:00Z"
    elif change == "provenance":
        p = w.path / f"artifacts/epic-tasks/{w.key}-E002.md"
        parts = p.read_text().split("---", 2)
        data = yaml.safe_load(parts[1])
        data["source_revision"] = "stale"
        p.write_text("---\n" + yaml.safe_dump(data) + "---" + parts[2])
    elif change == "policy":
        p = w.path / "workloads/policies/rhai/v1/review.md"
        p.write_text(p.read_text() + "\nChanged after planning.\n")
    else:
        w.write(f"artifacts/epic-reviews/{w.key}-decomp-review.md", score=12,
                criterion_scores=[0, 2, 2, 2, 2, 2, 2], recommendation="revise", **{"pass": False}, issues=[])
    assert w.submit(expected=None).returncode != 0
    assert not w.jira.writes


@pytest.mark.parametrize("operation", ["create", "attachment"])
def test_cli_recovers_persisted_effect_after_lost_http_response(workspace, operation):
    w = workspace
    w.pipeline(rationales=False, main_gate=False)
    w.jira.drop_after = ("POST", "/issue" if operation == "create" else "/issue/RHAI-1/attachments")
    assert w.submit(expected=None).returncode != 0
    assert "epic-creator-auto-decomposed" not in w.jira.issues[w.key]["fields"]["labels"]
    assert len([r for r in w.jira.writes if r["path"] == "/rest/api/3/issue"]) == (1 if operation == "create" else 2)
    w.submit()
    assert len(w.jira.issues) == 3
    for key, issue in w.jira.issues.items():
        filenames = [a["filename"] for a in issue["fields"]["attachment"]]
        assert len(filenames) == len(set(filenames)) or key == w.key
    before = deepcopy(w.jira.writes)
    w.submit()
    assert w.jira.writes == before


def test_cli_duplicate_search_match_stops_without_mutation(workspace):
    w = workspace
    w.pipeline(rationales=False, main_gate=False)
    w.submit()
    w.jira.issues["RHAI-99"] = deepcopy(w.jira.issues["RHAI-1"])
    w.jira.issues["RHAI-99"]["key"] = "RHAI-99"
    before = deepcopy(w.jira.writes)
    result = w.submit(expected=None)
    assert result.returncode != 0 and "duplicate" in result.stderr
    assert w.jira.writes == before


@pytest.mark.parametrize("invalid_gate", ["missing", "implementation", "not_dependency"])
def test_cli_rejects_invalid_main_investigation_gate(workspace, invalid_gate):
    w = workspace
    w.pipeline(rationales=True, main_gate=True)
    if invalid_gate == "implementation":
        w.write(f"artifacts/epic-tasks/{w.key}-E004.md", body="## Description\nIndependent implementation.\n",
                epic_id=f"{w.key}-E004", parent_strat=w.key, title="Independent implementation",
                type="Implementation", component="Dashboard", team="Dashboard", priority="P1", dependencies=[])
        w.write(f"artifacts/epic-tasks/{w.key}-decomposition.md", epic_count=3)
        changes = {"gated_by": f"{w.key}-E004", "dependencies": [f"{w.key}-E001", f"{w.key}-E004"]}
    else:
        changes = {"gated_by": f"{w.key}-E099"} if invalid_gate == "missing" else {"dependencies": []}
    w.write(f"artifacts/epic-tasks/{w.key}-E002.md", **changes)
    result = w.submit("--dry-run", expected=None)
    assert result.returncode != 0 and "Investigation gate" in result.stderr
    assert not w.jira.writes


def test_cli_intake_uses_newest_exact_attachment_and_rejects_fallback(workspace):
    w = workspace
    w.cli("fetch_strategy", "fetch", "project = RHOAIENG", "--ids-file", "tmp/discovered.txt",
          "--skip-if-processed", "--workload", w.workload_id)
    assert (w.path / "tmp/discovered.txt").read_text().strip() == w.key
    assert w.metadata(f"artifacts/strat-tasks/{w.key}.md")["attachment_id"] == "2"
    assert not any(r["path"] == "/attachment/1" for r in w.jira.requests)
    w.jira.issues[w.key]["fields"]["attachment"] = []
    (w.path / f"artifacts/strat-tasks/{w.key}.md").unlink()
    result = w.cli("fetch_strategy", "fetch-one", w.key, "--workload", w.workload_id)
    assert "no strategy attachment" in result.stderr
    assert not (w.path / f"artifacts/strat-tasks/{w.key}.md").exists()
    before = len(w.jira.requests)
    w.cli("fetch_strategy", "fetch-one", w.key, "--workload", "unknown-workload", expected=1)
    assert len(w.jira.requests) == before
    assert not w.jira.writes


def model_worker(workspace, phase, agent):
    """Use real model content, retaining real CLI validation and local-only Jira."""
    if phase == "FETCH":
        workspace.deterministic_worker(phase, agent)
        return
    variables = dict(line.split("=", 1) for line in agent["vars"].splitlines())
    shared = (workspace.path / agent["prompt_file"]).read_text()
    policy = Path(variables["POLICY_FILE"]).read_text()
    template = Path(variables["ARTIFACT_TEMPLATE"]).read_text()
    inputs = {str(p.relative_to(workspace.path)): p.read_text()
              for directory in [workspace.path / "artifacts", workspace.path / ".context"]
              for p in sorted(directory.rglob("*")) if p.is_file() and p.suffix in (".md", ".txt")}
    schemas = {name: workspace.cli("frontmatter", "schema", name, "--workload", workspace.workload_id).stdout
               for name in ("epic-task", "decomp-summary", "decomp-review")}
    prompt = (
        f"Run the {phase} worker for the supplied strategy. Follow the actual shared prompt and selected policy below.\n"
        "You have no tools. Return only the requested artifact edits as structured output. "
        "Each artifact has a workspace-relative path, its complete Markdown body WITHOUT YAML delimiters, "
        "and frontmatter_json (a JSON object serialized as a string). For rationale sidecars this is '{}'. "
        "A trusted local adapter applies metadata through frontmatter.py --source, inheriting provenance. "
        "Supply only producer fields, not workload/source provenance or computed ai_implementability fields. "
        "Use the exact full strategy key for all IDs, paths and dependencies. Include the required rationale sidecars. "
        "Review in a fresh context: judge the supplied artifacts against the rubric; do not invent passing scores. "
        "Revision must follow the actual review and set revised=false if no changes are required.\n\n"
        + agent["vars"] + "\nSHARED PROMPT\n" + shared + "\nSELECTED POLICY\n" + policy
        + "\nARTIFACT TEMPLATE\n" + template + "\nSCHEMAS\n" + json.dumps(schemas)
        + "\nWORKSPACE INPUTS\n" + json.dumps(inputs)
    )
    schema = {"type": "object", "additionalProperties": False, "required": ["artifacts"],
              "properties": {"artifacts": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False,
                  "required": ["path", "body", "frontmatter_json"],
                  "properties": {name: {"type": "string"}
                                 for name in ("path", "body", "frontmatter_json")}}}}}
    env = {k: v for k, v in os.environ.items() if not k.startswith("JIRA_")}
    command = ["claude", "--print", "--output-format", "json", "--tools", "",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--setting-sources", "", "--no-session-persistence", "--permission-mode", "dontAsk",
               "--max-budget-usd", "3", "--json-schema", json.dumps(schema)]
    print(f"Model phase {phase} started", flush=True)
    started = time.monotonic()
    result = subprocess.run(command, cwd=workspace.path, env=env, input=prompt,
                            capture_output=True, text=True, timeout=300)
    directory = workspace.path / "model-evidence"
    directory.mkdir(exist_ok=True)
    (directory / f"{phase}.json").write_text(result.stdout)
    (directory / f"{phase}.stderr").write_text(result.stderr)
    print(f"Model phase {phase}: exit {result.returncode} after {time.monotonic()-started:.1f}s", flush=True)
    assert result.returncode == 0, result.stderr + result.stdout
    response = json.loads(result.stdout)
    assert not response.get("is_error"), response
    output = response.get("structured_output") or json.loads(response["result"])
    for artifact in output["artifacts"]:
        relative = artifact["path"]
        target = (workspace.path / relative).resolve()
        assert target.is_relative_to(workspace.path.resolve())
        assert relative.startswith((f"artifacts/epic-tasks/{workspace.key}-",
                                    f"artifacts/epic-reviews/{workspace.key}-"))
        assert target.suffix == ".md"
        workspace.write(relative, body=artifact["body"], **json.loads(artifact["frontmatter_json"]))


@pytest.mark.skipif(os.environ.get("WORKLOAD_E2E_MODEL") != "1", reason="opt-in real model planning")
def test_tool_free_model_planning_through_local_submission(workspace):
    w = workspace
    fixture = ROOT / "eval/cases/case-009-triage-trivial-rename/artifacts/strat-tasks/RHAISTRAT-9009.md"
    body = fixture.read_text().split("---", 2)[2].strip()
    w.jira.contents["2"] = body
    w.jira.issues[w.key]["fields"]["summary"] = "Rename Dashboard navigation to Projects"
    architecture = w.path / ".context/architecture-context/architecture/rhoai-3.0"
    architecture.mkdir(parents=True)
    shutil.copy2(ROOT / "eval/shared/architecture-context/dashboard.md", architecture / "dashboard.md")
    phases = w.pipeline(worker=lambda phase, agent: model_worker(w, phase, agent))
    assert {"DECOMPOSE", "REVIEW_DECOMP", "REVISE_DECOMP", "REPORT"} <= set(phases)
    summary = w.metadata(f"artifacts/epic-tasks/{w.key}-decomposition.md")
    assert summary["triage"] == "below-threshold" and summary["epic_count"] == 1
    assert (w.path / f"artifacts/epic-tasks/{w.key}-E001-ai-signals.md").exists()
    before = len(w.jira.requests)
    w.submit("--dry-run", env={k: v for k, v in w.env.items() if not k.startswith("JIRA_")})
    assert len(w.jira.requests) == before
    w.submit()
    assert set(w.jira.issues) == {w.key, "RHAI-1"}
    assert w.jira.issues["RHAI-1"]["fields"]["parent"] == {"key": w.key}
