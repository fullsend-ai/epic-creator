"""Synthetic policy and dispatcher contracts; no live Jira or model calls."""
import json
from pathlib import Path
import shutil
import subprocess
import socket
import urllib.request
import sys
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import artifact_utils as artifacts
import pipeline_state as pipeline
import workload
import frontmatter


def selected():
    return workload.get_workload("rhai-initiative")


def source(key="RHOAIENG-9001"):
    w = selected()
    return dict(strat_id=key, title="Synthetic strategy", workload_id=w.id,
                labels=list(w.ready_labels),
                workload_version=w.version, workload_digest=w.digest,
                planning_digest=w.planning.digest, source_key=key,
                source_project=w.project, source_issue_type=w.issue_type,
                source_revision="2026-10-13T10:00:00Z",
                attachment_id="9001", attachment_created="2026-10-13T09:00:00Z")


def write_source(key="RHOAIENG-9001"):
    path = Path(f"artifacts/strat-tasks/{key}.md")
    artifacts.write_frontmatter(path, source(key), "strat-task", body="# Synthetic strategy\n")
    return path


def init(monkeypatch, tmp_path, capsys, key="RHOAIENG-9001"):
    monkeypatch.chdir(tmp_path)
    pipeline.cmd_init(["--workload", "rhai-initiative"])
    pipeline._write_ids("tmp/pipeline-active-ids.txt", [key])
    capsys.readouterr()


def test_shipped_workloads_have_same_versioned_rhai_policies():
    first, second = selected(), workload.get_workload("rhaistrat-strategy")
    assert first.planning.digest == second.planning.digest
    assert first.planning.component_project == "RHAI"
    assert first.planning.pass_score == 10
    assert first.planning.max_score == 14
    assert "## Step 0: Triage" in first.planning.decompose.read_text()
    assert "### Criterion 7: Completeness" in first.planning.review.read_text()
    assert "### HLR Coverage issues" in first.planning.revise.read_text()
    assert "HLR Traceability Matrix" in first.planning.artifact_template.read_text()


def test_init_binds_workload_and_policy(monkeypatch, tmp_path, capsys):
    init(monkeypatch, tmp_path, capsys)
    data = pipeline._load_state()
    assert data["workload_id"] == selected().id
    assert data["workload_explicit"] is True
    assert data["planning_digest"] == selected().planning.digest


def test_default_init_keeps_legacy_artifacts(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    pipeline.cmd_init([])
    state = pipeline._load_state()
    assert state["workload_id"] == "rhaistrat-strategy"
    assert state["workload_explicit"] is False
    artifacts.write_frontmatter("artifacts/strat-tasks/RHAISTRAT-9001.md",
                               {"strat_id": "RHAISTRAT-9001", "title": "Legacy"}, "strat-task")
    pipeline._write_ids("tmp/pipeline-active-ids.txt", ["RHAISTRAT-9001"])
    pipeline.cmd_set_phase(["DECOMPOSE"])
    capsys.readouterr()
    pipeline.cmd_next_action([])
    action = yaml.safe_load(capsys.readouterr().out)
    assert action["action"] == "launch_wave"
    assert "POLICY_FILE=" in action["agents"][0]["vars"]


@pytest.mark.parametrize("phase,policy", [("DECOMPOSE", "decompose"),
    ("REVIEW_DECOMP", "review"), ("REVISE_DECOMP", "revise"),
    ("RE_REVIEW", "review"), ("RE_REVISE", "revise")])
def test_dispatch_selects_policy_for_every_agent_phase(monkeypatch, tmp_path, capsys, phase, policy):
    init(monkeypatch, tmp_path, capsys)
    write_source()
    pipeline._write_ids("tmp/pipeline-revise-ids.txt", ["RHOAIENG-9001"])
    pipeline.cmd_set_phase([phase])
    capsys.readouterr()
    pipeline.cmd_get_phase_config([])
    config = yaml.safe_load(capsys.readouterr().out)
    assert config["vars"]["POLICY_FILE"] == str(getattr(selected().planning, policy))
    assert config["vars"]["WORKLOAD_ID"] == selected().id


@pytest.mark.parametrize("field", ["workload_id", "workload_version", "workload_digest", "planning_digest"])
def test_generic_set_cannot_change_bound_identity(monkeypatch, tmp_path, capsys, field):
    init(monkeypatch, tmp_path, capsys)
    before = Path(pipeline.STATE_FILE).read_bytes()
    with pytest.raises(workload.WorkloadError):
        pipeline.cmd_set([f"{field}=changed"])
    assert Path(pipeline.STATE_FILE).read_bytes() == before


def test_resume_rejects_changed_policy_bytes(monkeypatch, tmp_path, capsys):
    registry = tmp_path / "repo" / "workloads"
    shutil.copytree(ROOT / "workloads", registry)
    monkeypatch.setattr(workload, "WORKLOADS_DIR", registry)
    init(monkeypatch, tmp_path, capsys)
    selected().planning.decompose.write_text("Changed policy\n")
    with pytest.raises(workload.WorkloadError, match="policy|planning"):
        pipeline._load_state()


@pytest.mark.parametrize("unsafe", ["../outside.md", "/tmp/outside.md", "workloads/policies/missing.md"])
def test_registry_rejects_unsafe_or_missing_policy(monkeypatch, tmp_path, unsafe):
    registry = tmp_path / "workloads"
    shutil.copytree(ROOT / "workloads", registry)
    data = yaml.safe_load((registry / "rhai-initiative.yaml").read_text())
    data["planning"]["decompose"] = unsafe
    (registry / "rhai-initiative.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr(workload, "WORKLOADS_DIR", registry)
    with pytest.raises(workload.WorkloadError):
        workload.load_registry()


def test_completed_source_without_metadata_cannot_release_fetch(monkeypatch, tmp_path, capsys):
    init(monkeypatch, tmp_path, capsys)
    path = Path("artifacts/strat-tasks/RHOAIENG-9001.md")
    path.parent.mkdir(parents=True)
    path.write_text("Already cached but unidentifiable\n")
    pipeline.cmd_set_phase(["FETCH"])
    with pytest.raises(workload.WorkloadError):
        pipeline.cmd_next_action([])
    assert pipeline._load_state()["phase"] == "FETCH"


def test_review_gate_rejects_zero_dimension_despite_high_total():
    p = selected().planning
    assert p.review_result([2, 2, 2, 2, 2, 2, 0]) == (12, False)
    assert p.review_result([2, 2, 2, 1, 1, 1, 1]) == (10, True)


@pytest.mark.parametrize("scores", [[2]*6, [2]*6+[True], [2]*6+[3], [2]*6+[-1]])
def test_review_gate_rejects_invalid_dimensions(scores):
    with pytest.raises(workload.WorkloadError):
        selected().planning.review_result(scores)


def generated(key="RHOAIENG-9001", schema="decomp-summary"):
    metadata = {k: v for k, v in source(key).items() if k in artifacts._WORKLOAD_FIELDS}
    if schema == "decomp-summary":
        return dict(metadata, parent_strat=key, epic_count=1, critical_path_length=1,
                    decompose_complete=True)
    if schema == "epic-task":
        return dict(metadata, epic_id=f"{key}-E001", title="Synthetic epic", parent_strat=key,
                    component="synthetic", team="synthetic", type="Implementation", priority="P1")
    return dict(metadata, strat_id=key, score=14, **{"pass": True}, recommendation="accept",
                criterion_scores=[2]*7, issues=[])


def write_decomposition(key="RHOAIENG-9001"):
    for schema, path in [("decomp-summary", f"artifacts/epic-tasks/{key}-decomposition.md"),
                         ("epic-task", f"artifacts/epic-tasks/{key}-E001.md")]:
        artifacts.write_frontmatter(path, generated(key, schema), schema, body="Synthetic output\n")


def cli_set(monkeypatch, path, *values, source_path="artifacts/strat-tasks/RHOAIENG-9001.md"):
    monkeypatch.setattr(sys, "argv", ["frontmatter.py", "set", "--source", source_path, str(path), *values])
    frontmatter.main()


def test_cli_inherits_source_provenance_without_handwritten_metadata(monkeypatch, tmp_path, capsys):
    init(monkeypatch, tmp_path, capsys)
    write_source()
    path = Path("artifacts/epic-tasks/RHOAIENG-9001-decomposition.md")
    cli_set(monkeypatch, path, "parent_strat=RHOAIENG-9001", "epic_count=1", "critical_path_length=1")
    data, _ = artifacts.read_frontmatter_validated(path, "decomp-summary")
    assert data["source_revision"] == source()["source_revision"]
    assert data["planning_digest"] == selected().planning.digest


def test_cli_rejects_conflicting_explicit_provenance_before_write(monkeypatch, tmp_path, capsys):
    init(monkeypatch, tmp_path, capsys)
    write_source()
    path = Path("artifacts/epic-tasks/RHOAIENG-9001-decomposition.md")
    with pytest.raises(SystemExit):
        cli_set(monkeypatch, path, "parent_strat=RHOAIENG-9001", "epic_count=1",
                "critical_path_length=1", "source_revision=wrong")
    assert not path.exists()


def test_selected_review_schema_produces_a_valid_failing_review(monkeypatch, tmp_path, capsys):
    init(monkeypatch, tmp_path, capsys)
    write_source()
    monkeypatch.setattr(sys, "argv", ["frontmatter.py", "schema", "decomp-review",
                                    "--workload", "rhai-initiative"])
    frontmatter.main()
    schema = yaml.safe_load(capsys.readouterr().out)
    criteria = schema["optional"]["issues"]["items"]["fields"]["criterion"]["enum"]
    assert criteria[3] == "Type Correctness"
    assert "criterion_scores" in schema["required"]
    path = Path("artifacts/epic-reviews/RHOAIENG-9001-decomp-review.md")
    issue = {"severity": "major", "criterion": criteria[3], "description": "Missing Investigation gate"}
    cli_set(monkeypatch, path, "strat_id=RHOAIENG-9001", "score=11", "pass=false",
            "recommendation=revise", "criterion_scores=[2,2,2,0,2,1,2]",
            "issues=" + json.dumps([issue]))
    review, _ = artifacts.read_frontmatter_validated(path, "decomp-review")
    assert review["pass"] is False
    assert review["issues"][0]["criterion"] == "Type Correctness"


def test_cli_rejects_noncanonical_typed_component_without_changing_artifact(monkeypatch, tmp_path, capsys):
    registry = tmp_path / "repo/workloads"
    shutil.copytree(ROOT / "workloads", registry)
    cache = registry.parent / ".context/rhai-components.txt"
    cache.parent.mkdir()
    cache.write_text("Model Validation\n")
    monkeypatch.setattr(workload, "WORKLOADS_DIR", registry)
    init(monkeypatch, tmp_path, capsys)
    monkeypatch.setattr(sys, "argv", ["frontmatter.py", "schema", "epic-task",
                                    "--workload", "rhai-initiative"])
    frontmatter.main()
    assert yaml.safe_load(capsys.readouterr().out)["required"]["component"]["enum"] == ["Model Validation"]
    write_source()
    path = Path("artifacts/epic-tasks/RHOAIENG-9001-E001.md")
    data = generated(schema="epic-task")
    data["component"] = "Model Validation"
    artifacts.write_frontmatter(path, data, "epic-task", body="Reviewed work\n")
    before = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        cli_set(monkeypatch, path, "component=model-registry")
    assert error.value.code == 1
    assert path.read_bytes() == before
    assert "Model Validation" in capsys.readouterr().err


@pytest.mark.parametrize("branch", [False, True])
def test_stage_rejects_missing_epic_provenance(monkeypatch, tmp_path, capsys, branch):
    init(monkeypatch, tmp_path, capsys)
    write_source()
    write_decomposition()
    path = Path("artifacts/epic-tasks/RHOAIENG-9001-E001.md")
    if branch:
        path = Path("artifacts/epic-tasks/RHOAIENG-9001-BRANCH-A-E002.md")
        data = generated(schema="epic-task")
        data["epic_id"] = path.stem
        artifacts.write_frontmatter(path, data, "epic-task")
    data, body = artifacts.read_frontmatter(path)
    del data["planning_digest"]
    artifacts.write_frontmatter(path, data, "epic-task", body=body)
    pipeline.cmd_set_phase(["DECOMPOSE"])
    with pytest.raises(workload.WorkloadError, match="provenance"):
        pipeline.cmd_next_action([])
    assert pipeline._load_state()["phase"] == "DECOMPOSE"


def test_completed_summary_without_epics_cannot_release_stage(monkeypatch, tmp_path, capsys):
    init(monkeypatch, tmp_path, capsys)
    write_source()
    artifacts.write_frontmatter("artifacts/epic-tasks/RHOAIENG-9001-decomposition.md", generated(), "decomp-summary")
    pipeline.cmd_set_phase(["DECOMPOSE"])
    with pytest.raises(workload.WorkloadError, match="count"):
        pipeline.cmd_next_action([])


def test_source_revision_change_is_rejected_on_resume(monkeypatch, tmp_path, capsys):
    init(monkeypatch, tmp_path, capsys)
    path = write_source()
    pipeline.cmd_set_phase(["FETCH"])
    pipeline.cmd_next_action([])
    artifacts.update_frontmatter(path, {"source_revision": "2026-10-14T10:00:00Z"}, "strat-task")
    with pytest.raises(workload.WorkloadError, match="revision"):
        pipeline.cmd_next_action([])


@pytest.mark.parametrize("changes", [{"score": 13}, {"pass": False}, {"recommendation": "revise"},
    {"criterion_scores": [2]*6+[0]},
    {"issues": [{"severity": "major", "criterion": "DAG Coherence", "description": "Synthetic defect"}]}])
def test_review_metadata_must_agree_with_selected_rubric(monkeypatch, tmp_path, capsys, changes):
    init(monkeypatch, tmp_path, capsys)
    data = generated(schema="decomp-review")
    data.update(changes)
    with pytest.raises(artifacts.ValidationError):
        artifacts.write_frontmatter("artifacts/epic-reviews/RHOAIENG-9001-decomp-review.md", data, "decomp-review")


@pytest.mark.parametrize("key", ["../RHOAIENG-9001", "RHOAIENG-9001;touch /tmp/owned", "RHAISTRAT-9001"])
def test_dispatch_rejects_unsafe_or_wrong_workload_ids_before_launch(monkeypatch, tmp_path, capsys, key):
    init(monkeypatch, tmp_path, capsys, key)
    pipeline.cmd_set_phase(["FETCH"])
    with pytest.raises(workload.WorkloadError):
        pipeline.cmd_next_action([])


def test_report_records_workload_and_source_revision(monkeypatch, tmp_path, capsys):
    import generate_run_report
    init(monkeypatch, tmp_path, capsys)
    write_source()
    write_decomposition()
    artifacts.write_frontmatter("artifacts/epic-reviews/RHOAIENG-9001-decomp-review.md",
                               generated(schema="decomp-review"), "decomp-review")
    monkeypatch.setattr(sys, "argv", ["generate_run_report.py", "--start-time", "2026-10-13T10:00:00Z", "RHOAIENG-9001"])
    generate_run_report.main()
    report = yaml.safe_load(next(Path("artifacts/decompose-runs").glob("*.yaml")).read_text())
    assert report["workload_id"] == "rhai-initiative"
    assert report["planning_digest"] == selected().planning.digest
    assert report["results"][0]["source_revision"] == source()["source_revision"]


def test_cached_source_requires_original_approval_evidence(monkeypatch, tmp_path, capsys):
    init(monkeypatch, tmp_path, capsys)
    path = write_source()
    artifacts.update_frontmatter(path, {"labels": []}, "strat-task")
    pipeline.cmd_set_phase(["FETCH"])
    with pytest.raises(workload.WorkloadError, match="approval"):
        pipeline.cmd_next_action([])


def test_component_fetch_uses_selected_policy(monkeypatch, tmp_path, capsys):
    import fetch_components
    registry = tmp_path / "repo/workloads"
    shutil.copytree(ROOT / "workloads", registry)
    data = yaml.safe_load((registry / "rhai-initiative.yaml").read_text())
    data["planning"]["components"] = {"project": "EXAMPLE", "file": ".context/example-components.txt"}
    (registry / "rhai-initiative.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr(workload, "WORKLOADS_DIR", registry)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EPIC_SKIP_COMPONENT_FETCH", raising=False)
    monkeypatch.setattr(sys, "argv", ["fetch_components.py", "--workload", "rhai-initiative"])
    monkeypatch.setattr(fetch_components, "OUTPUT_PATH", str(tmp_path / ".context/legacy-components.txt"))
    with patch.object(fetch_components, "require_env", return_value=("https://jira.example.com", "u", "t")), \
         patch.object(fetch_components, "api_call_with_retry", return_value=[{"name": "Synthetic Component"}]) as api:
        fetch_components.main(["--workload", "rhai-initiative"])
    api.assert_called_once_with("https://jira.example.com", "/project/EXAMPLE/components", "u", "t")
    assert Path(".context/example-components.txt").read_text() == "Synthetic Component\n"


def test_report_preserves_missing_source_failure(monkeypatch, tmp_path, capsys):
    import generate_run_report
    init(monkeypatch, tmp_path, capsys)
    monkeypatch.setattr(sys, "argv", ["report", "--start-time", "2026-10-13T10:00:00Z", "RHOAIENG-9001"])
    generate_run_report.main()
    report = yaml.safe_load(next(Path("artifacts/decompose-runs").glob("*.yaml")).read_text())
    assert report["results"][0]["status"] == "missing"


@pytest.mark.parametrize("alternate", [False, True])
def test_full_dispatch_flow_is_local_and_preserves_revision_cycle(monkeypatch, tmp_path, capsys, alternate):
    import generate_run_report
    key, workload_id = "RHOAIENG-9001", "rhai-initiative"
    if alternate:
        registry = tmp_path / "repo/workloads"
        shutil.copytree(ROOT / "workloads", registry)
        data = yaml.safe_load((registry / "rhai-initiative.yaml").read_text())
        data["id"] = "synthetic-initiative"
        data["source"].update(project="EXAMPLE", id_pattern=r"^EXAMPLE-\d+$")
        data["planning"]["review_gate"]["pass_score"] = 11
        data["planning"]["decompose"] = "workloads/policies/example/v1/decompose.md"
        policy = registry / "policies/example/v1/decompose.md"
        policy.parent.mkdir(parents=True)
        policy.write_text("Synthetic alternate decomposition policy\n")
        (registry / "example.yaml").write_text(yaml.safe_dump(data))
        monkeypatch.setattr(workload, "WORKLOADS_DIR", registry)
        key, workload_id = "EXAMPLE-9001", "synthetic-initiative"
        configured = workload.get_workload(workload_id)
        monkeypatch.setattr(sys.modules[__name__], "selected", lambda: configured)
    monkeypatch.chdir(tmp_path)
    pipeline.cmd_init(["--workload", workload_id])
    pipeline._write_ids("tmp/pipeline-all-ids.txt", [key])
    pipeline._write_ids("tmp/pipeline-batch-1-ids.txt", [key])
    pipeline.cmd_set(["total_batches=1"])
    write_source(key)
    pipeline.cmd_set_phase(["BATCH_START"])
    capsys.readouterr()
    phases = []
    with patch.object(socket, "socket", side_effect=AssertionError("Network blocked")) as network, \
         patch.object(urllib.request, "urlopen", side_effect=AssertionError("HTTP blocked")) as http:
        for _ in range(15):
            pipeline.cmd_next_action([])
            action = yaml.safe_load(capsys.readouterr().out)
            if action["action"] == "done":
                break
            phase = action["phase"]
            phases.append(phase)
            if phase == "DECOMPOSE":
                assert f"WORKLOAD_ID={workload_id}" in action["agents"][0]["vars"]
                write_decomposition(key)
            elif phase == "REVIEW_DECOMP":
                data = generated(key, schema="decomp-review")
                data.update(score=7, **{"pass": False}, recommendation="revise", criterion_scores=[1]*7)
                artifacts.write_frontmatter(f"artifacts/epic-reviews/{key}-decomp-review.md", data, "decomp-review")
            elif phase == "REVISE_DECOMP":
                artifacts.update_frontmatter(f"artifacts/epic-tasks/{key}-decomposition.md", {"revised": True}, "decomp-summary")
            elif phase == "RE_REVIEW":
                artifacts.write_frontmatter(f"artifacts/epic-reviews/{key}-decomp-review.md", generated(key, schema="decomp-review"), "decomp-review")
            elif phase == "REPORT":
                monkeypatch.setattr(sys, "argv", ["report", "--start-time", "2026-10-13T10:00:00Z"])
                generate_run_report.main()
                Path(pipeline.DISPATCH_MARKER).write_text("REPORT")
                capsys.readouterr()
            else:
                pytest.fail(f"Unexpected phase: {phase}")
        else:
            pytest.fail("Dispatcher did not reach DONE")
        network.assert_not_called()
        http.assert_not_called()
    assert phases == ["DECOMPOSE", "REVIEW_DECOMP", "REVISE_DECOMP", "RE_REVIEW", "REPORT"]
    assert pipeline._load_state()["phase"] == "DONE"
    report = yaml.safe_load(next(Path("artifacts/decompose-runs").glob("*.yaml")).read_text())
    assert report["results"][0]["status"] == "passed"
    assert report["workload_id"] == workload_id
    assert report["review_pass_score"] == (11 if alternate else 10)


def test_ai_scoring_thresholds_come_from_selected_policy(monkeypatch, tmp_path):
    import compute_ai_scores
    registry = tmp_path / "workloads"
    shutil.copytree(ROOT / "workloads", registry)
    data = yaml.safe_load((registry / "rhai-initiative.yaml").read_text())
    scoring = registry.parent / data["planning"]["scoring"]
    rules = yaml.safe_load(scoring.read_text())
    rules["implementation"]["high_min"] = 2
    scoring.write_text(yaml.safe_dump(rules))
    monkeypatch.setattr(workload, "WORKLOADS_DIR", registry)
    assert compute_ai_scores.classify(2, selected().planning) == "High"
    assert compute_ai_scores.classify(-1, selected().planning) == "Low"


def test_second_synthetic_workload_changes_dispatch_without_shared_code_edit(monkeypatch, tmp_path, capsys):
    registry = tmp_path / "repo/workloads"
    shutil.copytree(ROOT / "workloads", registry)
    data = yaml.safe_load((registry / "rhai-initiative.yaml").read_text())
    data["id"] = "synthetic-initiative"
    data["source"].update(project="EXAMPLE", id_pattern=r"^EXAMPLE-\d+$")
    data["planning"]["decompose"] = "workloads/policies/example/v1/decompose.md"
    policy = registry / "policies/example/v1/decompose.md"
    policy.parent.mkdir(parents=True)
    policy.write_text("Synthetic alternate decomposition policy\n")
    (registry / "example.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr(workload, "WORKLOADS_DIR", registry)
    monkeypatch.chdir(tmp_path)
    pipeline.cmd_init(["--workload", "synthetic-initiative"])
    pipeline.cmd_set_phase(["DECOMPOSE"])
    capsys.readouterr()
    pipeline.cmd_get_phase_config([])
    config = yaml.safe_load(capsys.readouterr().out)
    assert config["vars"]["WORKLOAD_ID"] == "synthetic-initiative"
    assert config["vars"]["POLICY_FILE"] == str(policy.resolve())


def test_eval_staging_copies_workload_registry_into_isolated_case(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "workloads", repo / "workloads")
    case = tmp_path / "case"
    (case / ".context").mkdir(parents=True)
    (case / ".context/rhai-components.txt").write_text("Synthetic\n")
    (case / "artifacts/strat-tasks").mkdir(parents=True)
    (case / "input.yaml").write_text("strat_id: RHAISTRAT-9001\n")
    result = subprocess.run(["bash", str(ROOT / "eval/scripts/stage-case.sh")],
        env={"PATH": "/usr/bin:/bin", "CASE_WORKSPACE": str(case), "AGENT_EVAL_PROJECT_ROOT": str(repo)},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (case / "workloads/rhai-initiative.yaml").is_file()
    assert not (case / "workloads").is_symlink()
    assert (case / ".context/rhai-components.txt").read_text() == "Synthetic\n"
