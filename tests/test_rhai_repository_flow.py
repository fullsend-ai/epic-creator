import json
from pathlib import Path
import sys

import pytest
import yaml

from repository_fixtures import ROOT, configured, git_source
import rhai_config
import pipeline_state


def test_enabled_rhai_configuration_inserts_repository_stage(monkeypatch, tmp_path):
    selected = configured(git_source(tmp_path))
    monkeypatch.setattr(rhai_config, "load_config", lambda *a, **k: selected)
    monkeypatch.chdir(tmp_path)
    phase, _ = pipeline_state.advance({"phase": "FETCH"}, dry_run=True)
    assert phase == "PLAN_REPOSITORIES"


def test_disabled_rhai_configuration_keeps_legacy_phase_sequence(monkeypatch):
    selected = rhai_config.load_config()
    assert selected.repository_policy is None
    monkeypatch.setattr(rhai_config, "load_config", lambda *a, **k: selected)

    phase, _ = pipeline_state.advance({"phase": "FETCH"}, dry_run=True)
    assert phase == "DECOMPOSE"
    phase, _ = pipeline_state.advance({"phase": "DECOMPOSE"}, dry_run=True)
    assert phase == "REVIEW_DECOMP"
