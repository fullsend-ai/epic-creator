"""Real CLI→HTTPS contracts using prepared agent output, without live Jira writes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

import pytest
import yaml

from repository_fixtures import ROOT, git, git_source, policy, prepared_architecture, proposal, epic_metadata
from jira_https import certificate, local_jira  # noqa: F401 - pytest fixtures


class Workspace:
    def __init__(self, path, jira, certificate, *, cross=False):
        self.path, self.jira, self.key = path, jira, "RHAISTRAT-9001"
        self.commands = []
        for name in ("scripts", "skills", "config"):
            shutil.copytree(ROOT / name, path / name)
        prepared_architecture(path)
        repo = git_source(path)
        (repo / "mapping.md").write_text("Manager and cloudmanager changes belong in operator. Dashboard UI changes belong in dashboard.\n")
        git(repo, "add", ".")
        git(repo, "commit", "--signoff", "-m", "Dashboard ownership fixture")
        self.sources = repo
        config = yaml.safe_load((path / "config/rhai-repositories.yaml").read_text())
        config.update(enabled=True, repository_policy=policy(repo))
        (path / "config/rhai-repositories.yaml").write_text(yaml.safe_dump(config))
        text = "HLR-1: Manager logs.\nHLR-2: Cloudmanager logs.\nAC-1: Manager log tests pass.\nAC-2: Cloudmanager log tests pass.\n"
        if cross:
            text += "HLR-3: Dashboard UI shows logging status.\nAC-3: Dashboard UI status test passes.\n"
        jira.contents.update({"1": "Old source body", "2": text})
        attachment = dict(id="2", filename=f"{self.key}-strategy.md", created="2026-10-18T01:00:00Z", content=f"{jira.url}/attachment/2")
        old = dict(attachment, id="1", created="2026-10-17T01:00:00Z", content=f"{jira.url}/attachment/1")
        jira.issues[self.key] = {"key": self.key, "fields": {
            "summary": "Operator logging", "description": "Short source description",
            "project": {"key": "RHAISTRAT"}, "issuetype": {"name": "Feature"},
            "status": {"name": "Approved"}, "priority": {"name": "Major"},
            "labels": ["strat-creator-rubric-pass", "strat-creator-human-sign-off"],
            "assignee": {"accountId": "synthetic-account"}, "attachment": [old, attachment], "issuelinks": []}}
        jira.components = ["Operator", "Cloudmanager", "Dashboard"]
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

    def pipeline(self, worker=None, *, expected_status="passed", **options):
        self.cli("fetch_components")
        self.cli("pipeline_state", "init", "--headless")
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
                    worker(phase, agent, **options)
                self.cli("pipeline_state", "wait-for-wave")
        else:
            pytest.fail("Pipeline failed to terminate")
        assert self.metadata(f"artifacts/strat-tasks/{self.key}.md")["attachment_id"] == "2"
        report = yaml.safe_load(next((self.path / "artifacts/decompose-runs").glob("*.yaml")).read_text())
        assert report["results"][0]["status"] == expected_status
        assert yaml.safe_load((self.path / "tmp/pipeline-state.yaml").read_text())["phase"] == "DONE"
        return phases

    def submit(self, *args, expected=0, env=None):
        return self.cli("submit", *args, self.key,
                        expected=expected, env=env)



def prepared_worker(w, phase, agent, *, cross=False):
    if phase == "FETCH":
        w.cli("fetch_strategy", "fetch-one", w.key)
    elif phase == "PLAN_REPOSITORIES":
        locations = w.path / "artifacts/repository-plans"
        context = json.loads((locations / f"{w.key}-context.json").read_text())
        result = proposal(context)
        result["source_key"] = w.key
        if "mapping.md" not in context["sources"][0]["files"]:
            for item in result["work_items"]:
                item["resolution"]["evidence_refs"] = [{"source_id": "mapping", "path": "ownership.json",
                    "quote": '"operator": {"modules": ["manager", "cloudmanager"]}'}]
        if cross:
            result["requirements"] += [
                {"id": "HLR-3", "kind": "requirement", "quote": "HLR-3: Dashboard UI shows logging status."},
                {"id": "AC-3", "kind": "acceptance", "quote": "AC-3: Dashboard UI status test passes."}]
            item = deepcopy(result["work_items"][0])
            item.update(id="W3", description="Dashboard UI status", component="Dashboard", team="Dashboard",
                        planned_pr="PR-dashboard",
                        requirement_refs=["HLR-3"], acceptance_refs=["AC-3"],
                        acceptance_criteria=[{"source_ref": "AC-3", "text": "Dashboard UI status test passes."}], depends_on=["W1"])
            json_only = "mapping.md" not in context["sources"][0]["files"]
            item["resolution"] = {"status": "resolved", "target_key": "dashboard", "evidence_refs": [
                {"source_id": "mapping", "path": "ownership.json" if json_only else "mapping.md",
                 "quote": '"dashboard": {"modules": ["web UI"]}' if json_only else "Dashboard UI changes belong in dashboard."}]}
            result["work_items"].append(item)
        (locations / f"{w.key}-proposal.json").write_text(json.dumps(result))
        w.cli("repository_plan", "finalize",
              "--source", w.key)
    elif phase == "DECOMPOSE":
        plan = json.loads((w.path / f"artifacts/repository-plans/{w.key}.json").read_text())
        for fields in epic_metadata(plan):
            scope = next(s for s in plan["scopes"] if s["scope_id"] == fields["repository_scope"])
            items = [item for item in plan["work_items"] if item["id"] in scope["work_item_ids"]]
            body = "## Description\n" + "\n".join(item["description"] for item in items)
            body += "\n\n## Acceptance Criteria\n" + "\n".join(ac["text"] for item in items for ac in item["acceptance_criteria"])
            w.write(f"artifacts/epic-tasks/{fields['epic_id']}.md", body=body, **fields)
            (w.path / f"artifacts/epic-tasks/{fields['epic_id']}-ai-signals.md").write_text("Synthetic prepared signal rationale.\n")
        w.write(f"artifacts/epic-tasks/{w.key}-decomposition.md", body="Source-wide integration tests cover all scopes.\n",
                parent_strat=w.key, epic_count=len(plan["scopes"]), critical_path_length=len(plan["scopes"]),
                mapping_snapshot_digest=plan["mapping_snapshot_digest"], decompose_complete=True)
    elif phase in ("REVIEW_DECOMP", "RE_REVIEW"):
        w.write(f"artifacts/epic-reviews/{w.key}-decomp-review.md", body="Prepared contract review; not a model review.\n",
                strat_id=w.key, score=14, recommendation="accept", **{"pass": True})
    elif phase == "REVISE_DECOMP":
        w.write(f"artifacts/epic-tasks/{w.key}-decomposition.md", revised=False)
    else:
        raise AssertionError(phase)



@pytest.mark.parametrize("cross", [False, True])
def test_complete_repository_creation_through_https(tmp_path, local_jira, certificate, cross):
    w = Workspace(tmp_path / "run", local_jira, certificate, cross=cross)
    phases = w.pipeline(worker=lambda phase, agent: prepared_worker(w, phase, agent, cross=cross))
    assert phases[:3] == ["FETCH", "PLAN_REPOSITORIES", "DECOMPOSE"]
    w.submit("--dry-run")
    assert not local_jira.writes
    w.submit()
    creates = [r["body"]["fields"] for r in local_jira.requests if r["method"] == "POST" and r["path"] == "/rest/api/3/issue"]
    assert len(creates) == (2 if cross else 1)
    assert {l for f in creates for l in f["labels"] if l.startswith("target-repo-")} == ({"target-repo-operator", "target-repo-dashboard"} if cross else {"target-repo-operator"})
    operator = next(f for f in creates if "target-repo-operator" in f["labels"])
    assert operator["components"] == [{"name": "Operator"}, {"name": "Cloudmanager"}]
    assert operator["parent"] == {"key": w.key}
    assert "Manager logging tests pass" in json.dumps(operator["description"])
    assert "Cloudmanager logging tests pass" in json.dumps(operator["description"])
    assert len([i for k,i in local_jira.issues.items() if k != w.key]) == len(creates)
    assert all(any("repository-evidence-" in a["filename"] for a in i["fields"]["attachment"]) for k,i in local_jira.issues.items() if k != w.key)
    if cross:
        assert ("Blocks", "RHAI-2", "RHAI-1") in local_jira.links
    before = deepcopy(local_jira.writes)
    w.submit()
    assert local_jira.writes == before


@pytest.mark.parametrize("change", ["attachment", "source-approval", "review", "mapping", "unknown-create"])
def test_cli_rejects_stale_input_and_recovers_lost_response(tmp_path, local_jira, certificate, change):
    w = Workspace(tmp_path / "run", local_jira, certificate)
    w.pipeline(worker=lambda phase, agent: prepared_worker(w, phase, agent))
    if change == "attachment":
        local_jira.contents["2"] += "New acceptance condition.\n"
    elif change == "source-approval":
        local_jira.issues[w.key]["fields"]["labels"].clear()
    elif change == "review":
        draft = w.path / f"artifacts/epic-tasks/{w.key}-E001.md"
        draft.write_text(draft.read_text() + "Change Dashboard source too.\n")
    elif change == "mapping":
        (w.sources / "mapping.md").write_text("New mapping\n")
        git(w.sources, "add", ".")
        git(w.sources, "commit", "--signoff", "-m", "Change mapping")
    else:
        local_jira.drop_after = ("POST", "/issue")
    w.submit(expected=1)
    if change == "unknown-create":
        assert "RHAI-1" in local_jira.issues
        w.submit()
        assert len([k for k in local_jira.issues if k != w.key]) == 1
    else:
        assert not local_jira.writes


def test_three_planned_prs_create_three_epics_in_two_repositories(tmp_path, local_jira, certificate):
    w = Workspace(tmp_path / "run", local_jira, certificate, cross=True)

    def worker(phase, agent):
        prepared_worker(w, phase, agent, cross=True)
        if phase == "PLAN_REPOSITORIES":
            path = w.path / f"artifacts/repository-plans/{w.key}-proposal.json"
            draft = json.loads(path.read_text())
            draft["work_items"][1].update(planned_pr="PR-cloudmanager", depends_on=["W1"])
            draft["work_items"][2]["depends_on"] = ["W2"]
            path.write_text(json.dumps(draft))
            w.cli("repository_plan", "finalize", "--source", w.key)

    w.pipeline(worker=worker)
    w.submit("--dry-run")
    assert not local_jira.writes
    w.submit()

    created = {key: issue["fields"] for key, issue in local_jira.issues.items() if key != w.key}
    assert len(created) == 3
    assert all(fields["issuetype"] == {"name": "Epic"} for fields in created.values())
    assert sum("target-repo-operator" in fields["labels"] for fields in created.values()) == 2
    assert sum("target-repo-dashboard" in fields["labels"] for fields in created.values()) == 1
    manager = next(key for key, fields in created.items()
                   if "Manager logging tests pass" in json.dumps(fields["description"]))
    cloudmanager = next(key for key, fields in created.items()
                        if "Cloudmanager logging tests pass" in json.dumps(fields["description"]))
    dashboard = next(key for key, fields in created.items()
                     if "Dashboard UI status test passes" in json.dumps(fields["description"]))
    assert ("Blocks", manager, cloudmanager) in local_jira.links
    assert ("Blocks", cloudmanager, dashboard) in local_jira.links
    before = deepcopy(local_jira.writes)
    w.submit()
    assert local_jira.writes == before


def test_unresolved_work_is_created_with_warning(tmp_path, local_jira, certificate):
    w = Workspace(tmp_path / "run", local_jira, certificate)
    def worker(phase, agent):
        prepared_worker(w, phase, agent)
        if phase == "PLAN_REPOSITORIES":
            directory = w.path / "artifacts/repository-plans"
            draft_path = directory / f"{w.key}-proposal.json"
            draft = json.loads(draft_path.read_text())
            for item in draft["work_items"]:
                item["resolution"] = {"status": "unresolved", "reason": "contribution_owner_unknown", "candidates": []}
            draft_path.write_text(json.dumps(draft))
            w.cli("repository_plan", "finalize", "--source", w.key)
    assert "DECOMPOSE" in w.pipeline(worker=worker, expected_status="partial")
    w.submit()
    fields = local_jira.issues["RHAI-1"]["fields"]
    assert not any(label.startswith("target-repo-") for label in fields["labels"])
    assert "Confirm and update the target repository" in json.dumps(fields["comment"])
    before = deepcopy(local_jira.writes)
    w.submit()
    assert local_jira.writes == before


def test_partial_plan_creates_eight_epics_and_keeps_source_incomplete(tmp_path, local_jira, certificate):
    from test_repository_partial import separate_prs
    from submit import STRAT_LABEL

    w = Workspace(tmp_path / "run", local_jira, certificate)

    def worker(phase, agent):
        prepared_worker(w, phase, agent)
        if phase == "PLAN_REPOSITORIES":
            path = w.path / f"artifacts/repository-plans/{w.key}-proposal.json"
            draft = separate_prs(json.loads(path.read_text()))
            path.write_text(json.dumps(draft))
            w.cli("repository_plan", "finalize", "--source", w.key)

    w.pipeline(worker=worker, expected_status="partial")
    w.submit("--dry-run")
    assert not local_jira.writes
    w.submit()
    created = {key: issue["fields"] for key, issue in local_jira.issues.items() if key != w.key}
    assert len(created) == 8
    assert all(fields["issuetype"] == {"name": "Epic"} for fields in created.values())
    assert sum("target-repo-operator" in fields["labels"] for fields in created.values()) == 4
    assert sum("target-repo-dashboard" in fields["labels"] for fields in created.values()) == 3
    assert STRAT_LABEL not in local_jira.issues[w.key]["fields"]["labels"]
    before = deepcopy(local_jira.writes)
    w.submit()
    assert local_jira.writes == before
