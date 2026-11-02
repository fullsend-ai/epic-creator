from repository_fixtures import write_artifact
import json
import hashlib
from pathlib import Path
import sys
import shutil

import pytest
import yaml

from repository_fixtures import ROOT, configured, git_source, proposal, strategy
import artifact_utils as artifacts
import check_decompose_progress as progress
import pipeline_state as pipeline
import pipeline_repositories
import repository_plan
import rhai_config


def setup_pipeline(monkeypatch, tmp_path, capsys):
    repo = git_source(tmp_path)
    selected = configured(repo)
    components = tmp_path / selected.planning.component_file
    components.parent.mkdir(parents=True, exist_ok=True)
    components.write_text("Operator\nCloudmanager\n")
    monkeypatch.setattr(rhai_config, "load_config", lambda *a, **k: selected)
    source = strategy(tmp_path)
    body = source.read_text()
    monkeypatch.chdir(tmp_path)
    metadata = dict(strat_id=source.stem, title="Operator logging", labels=["strat-creator-rubric-pass", "strat-creator-human-sign-off"],
                    rhai_config_digest=selected.digest, planning_digest=selected.planning.digest,
                    source_revision=hashlib.sha256(body.strip().encode()).hexdigest())
    write_artifact(source, metadata, "strat-task", body=body)
    monkeypatch.chdir(tmp_path)
    pipeline.cmd_init([])
    pipeline._write_ids("tmp/pipeline-active-ids.txt", [source.stem])
    pipeline.cmd_set_phase(["FETCH"])
    capsys.readouterr()
    return selected, source


def test_repository_stage_before_drafts_and_polling_is_atomic(monkeypatch, tmp_path, capsys):
    selected, source = setup_pipeline(monkeypatch, tmp_path, capsys)
    pipeline.cmd_next_action([])
    action = yaml.safe_load(capsys.readouterr().out)
    assert action["phase"] == "PLAN_REPOSITORIES"
    locations = repository_plan.paths("artifacts", source.stem)
    assert locations["context"].exists()
    assert progress.check_id("plan_repositories", source.stem) == "pending"
    context = json.loads(locations["context"].read_text())
    plan = repository_plan.finalize_repository_plan(context, proposal(context))
    repository_plan.atomic_json(locations["plan"], plan)
    assert progress.check_id("plan_repositories", source.stem) == "completed"
    assert not Path("artifacts/epic-tasks").exists()
    pipeline.cmd_next_action([])
    action = yaml.safe_load(capsys.readouterr().out)
    assert action["phase"] == "DECOMPOSE"
    assert f'MAPPING_SNAPSHOT_DIGEST={context["digest"]}' in action["agents"][0]["vars"]


def test_draft_not_launched_without_ready_repository_plan(monkeypatch, tmp_path, capsys):
    _, source = setup_pipeline(monkeypatch, tmp_path, capsys)
    pipeline.cmd_set_phase(["DECOMPOSE"])
    with pytest.raises(rhai_config.RepositoryError, match="[Rr]epository"):
        pipeline_repositories.validate_inputs(pipeline._load_state(), "DECOMPOSE", [source.stem])


def test_changed_mapping_snapshot_cannot_resume(monkeypatch, tmp_path, capsys):
    _, source = setup_pipeline(monkeypatch, tmp_path, capsys)
    pipeline.cmd_next_action([])
    capsys.readouterr()
    source.write_text(source.read_text() + "\nNew acceptance condition\n")
    with pytest.raises(rhai_config.RepositoryError, match="changed"):
        pipeline_repositories.validate_inputs(pipeline._load_state(), "PLAN_REPOSITORIES", [source.stem])


def test_one_blocked_repository_plan_does_not_stop_other_strategy(monkeypatch, tmp_path, capsys):
    _, source = setup_pipeline(monkeypatch, tmp_path, capsys)
    second = source.with_name("RHAISTRAT-9002.md")
    data, body = artifacts.read_frontmatter(source)
    data.update(strat_id=second.stem)
    write_artifact(second, data, "strat-task", body=body)
    pipeline._write_ids("tmp/pipeline-active-ids.txt", [source.stem, second.stem])
    pipeline.cmd_next_action([])
    capsys.readouterr()
    source.write_text(source.read_text() + "\nChanged input\n")
    valid = pipeline._validate_ids_individually(pipeline._load_state(), "PLAN_REPOSITORIES",
              "tmp/pipeline-active-ids.txt", pipeline._validate_inputs_one)
    assert valid == [second.stem]


def test_finalizer_rejects_noncanonical_planned_component(monkeypatch, tmp_path, capsys):
    selected, source = setup_pipeline(monkeypatch, tmp_path, capsys)
    pipeline.cmd_next_action([])
    locations = repository_plan.paths("artifacts", source.stem)
    context = json.loads(locations["context"].read_text())
    invalid = proposal(context)
    invalid["work_items"][0]["component"] = "unregistered"
    repository_plan.atomic_json(locations["proposal"], invalid)
    monkeypatch.setattr(repository_plan, "load_config", lambda: selected)
    monkeypatch.setattr(sys, "argv", ["repository_plan.py", "finalize", "--source", source.stem])
    assert repository_plan.main() == 1
    assert not locations["plan"].exists()
