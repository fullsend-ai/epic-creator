import hashlib
from pathlib import Path

import pytest

from repository_fixtures import configured, git_source
import artifact_utils
import fetch_strategy
import rhai_config


def issue(labels):
    return {"key":"RHAISTRAT-9001", "fields":{"summary":"Operator logging", "description":"HLR-1: Manager logs.",
        "project":{"key":"RHAISTRAT"}, "issuetype":{"name":"Feature"}, "labels":labels}}


def test_repository_source_is_bound_to_configuration_and_content(tmp_path, monkeypatch):
    selected=configured(git_source(tmp_path))
    monkeypatch.setattr(rhai_config,"load_config",lambda *a,**k:selected)
    path=fetch_strategy._write_strategy(issue(["strat-creator-rubric-pass","strat-creator-human-sign-off"]),tmp_path/"input")
    data,body=artifact_utils.read_frontmatter(path)
    assert data["rhai_config_digest"]==selected.digest
    assert data["planning_digest"]==selected.planning_digest
    assert data["source_revision"]==hashlib.sha256(body.strip().encode()).hexdigest()


def test_unapproved_source_is_rejected_before_artifact_write(tmp_path, monkeypatch):
    selected=configured(git_source(tmp_path))
    monkeypatch.setattr(rhai_config,"load_config",lambda *a,**k:selected)
    with pytest.raises(rhai_config.RepositoryError):
        fetch_strategy._write_strategy(issue([]),tmp_path/"input")
    assert not (tmp_path/"input").exists()


def test_modified_strategy_body_cannot_reuse_fetched_revision(monkeypatch, tmp_path, capsys):
    from test_repository_pipeline import setup_pipeline
    from pipeline_repositories import source_metadata
    from rhai_config import RepositoryError
    _, source = setup_pipeline(monkeypatch, tmp_path, capsys)
    source.write_text(source.read_text() + "Changed required outcome.\n")
    with pytest.raises(RepositoryError, match="revision"):
        source_metadata({}, source.stem)
