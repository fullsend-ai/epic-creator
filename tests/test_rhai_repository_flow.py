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
