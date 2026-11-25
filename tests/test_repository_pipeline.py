#!/usr/bin/env python3
"""Tests for the RESOLVE_REPOSITORIES phase and the repository-mode flag (ADR-0176)."""
import json
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pipeline_state  # noqa: E402
import repository_resolution  # noqa: E402
from check_decompose_progress import check_id  # noqa: E402
from pipeline_state import PHASE_CONFIG, PHASES, advance  # noqa: E402
from repository_resolution import FLAG, FlagError, repository_mode  # noqa: E402


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(FLAG, raising=False)
    (tmp_path / "tmp").mkdir()
    return tmp_path


def _init(monkeypatch, *args):
    pipeline_state.cmd_init(list(args))
    with open("tmp/pipeline-state.yaml") as f:
        return yaml.safe_load(f)


class TestFlag:
    @pytest.mark.parametrize("value,expected", [
        (None, False), ("", False), ("false", False), ("true", True)])
    def test_accepted_values(self, value, expected):
        env = {} if value is None else {FLAG: value}
        assert repository_mode(env) is expected

    @pytest.mark.parametrize("value", ["tru", "1", "YES", "TRUE", "on", " true"])
    def test_other_values_are_hard_errors(self, value):
        with pytest.raises(FlagError, match=FLAG):
            repository_mode({FLAG: value})


class TestInit:
    def test_flag_off_never_reads_resolver_config(self, workdir, monkeypatch):
        def fail(*a, **k):
            raise AssertionError("resolver config must not be read when off")
        monkeypatch.setattr(repository_resolution, "load_sources", fail)
        state = _init(monkeypatch)
        assert state["repository_mode"] is False
        assert "resolution_manifest" not in state

    def test_flag_off_survives_broken_resolver_config(self, workdir, monkeypatch):
        (workdir / "config").mkdir()
        (workdir / "config/resolver.yaml").write_text("sources: [broken\n")
        monkeypatch.setenv(FLAG, "false")
        assert _init(monkeypatch)["repository_mode"] is False

    def test_malformed_flag_stops_the_run(self, workdir, monkeypatch):
        monkeypatch.setenv(FLAG, "yes")
        with pytest.raises(SystemExit):
            _init(monkeypatch)
        assert not (workdir / "tmp/pipeline-state.yaml").exists()

    def test_flag_on_loads_sources_at_init(self, workdir, monkeypatch):
        monkeypatch.setenv(FLAG, "true")
        monkeypatch.setattr(repository_resolution, "load_sources",
                            lambda: "tmp/repo-resolution/sources/manifest.json")
        state = _init(monkeypatch)
        assert state["repository_mode"] is True
        assert state["resolution_manifest"].endswith("manifest.json")

    def test_flag_on_with_unloadable_sources_stops_the_run(self, workdir, monkeypatch,
                                                           capsys):
        from sources import SourceFetchError

        def fail():
            raise SourceFetchError("fetching https://example.com/arch failed")
        monkeypatch.setenv(FLAG, "true")
        monkeypatch.setattr(repository_resolution, "load_sources", fail)
        with pytest.raises(SystemExit):
            _init(monkeypatch)
        assert "ERROR [fetch]" in capsys.readouterr().err
        assert not (workdir / "tmp/pipeline-state.yaml").exists()


class TestSequence:
    def test_phase_is_registered(self):
        assert PHASES.index("FETCH") < PHASES.index("RESOLVE_REPOSITORIES") < \
            PHASES.index("DECOMPOSE")
        config = PHASE_CONFIG["RESOLVE_REPOSITORIES"]
        assert config["poll_phase"] == "resolve"
        assert os.path.exists(os.path.join(os.path.dirname(__file__), "..",
                                           config["prompt"]))

    def test_flag_off_keeps_legacy_sequence(self, workdir):
        state = {"phase": "FETCH", "repository_mode": False}
        assert advance(state, dry_run=True)[0] == "DECOMPOSE"

    def test_state_without_flag_keeps_legacy_sequence(self, workdir):
        assert advance({"phase": "FETCH"}, dry_run=True)[0] == "DECOMPOSE"

    def test_flag_on_resolves_before_decompose(self, workdir):
        state = {"phase": "FETCH", "repository_mode": True}
        assert advance(state, dry_run=True)[0] == "RESOLVE_REPOSITORIES"


class TestVerificationGate:
    def test_invalid_results_are_dropped_from_the_batch(self, workdir, monkeypatch):
        (workdir / "tmp/pipeline-active-ids.txt").write_text(
            "RHAISTRAT-1\nRHAISTRAT-2\nRHAISTRAT-3\n")
        verdicts = {"RHAISTRAT-1": [], "RHAISTRAT-2": ["quote not found"],
                    "RHAISTRAT-3": []}
        monkeypatch.setattr(repository_resolution, "verify_strategy",
                            lambda i: {"strat_id": i, "verified": not verdicts[i],
                                       "errors": verdicts[i]})
        nxt, summary = advance({"phase": "RESOLVE_REPOSITORIES",
                                "repository_mode": True})
        assert nxt == "DECOMPOSE"
        assert "verified=2 invalid=1" in summary
        assert (workdir / "tmp/pipeline-active-ids.txt").read_text().split() == \
            ["RHAISTRAT-1", "RHAISTRAT-3"]

    def test_source_failure_during_verification_stops(self, workdir, monkeypatch):
        (workdir / "tmp/pipeline-active-ids.txt").write_text("RHAISTRAT-1\n")

        def fail(i):
            raise OSError("manifest missing")
        monkeypatch.setattr(repository_resolution, "verify_strategy", fail)
        with pytest.raises(SystemExit):
            advance({"phase": "RESOLVE_REPOSITORIES", "repository_mode": True})


class TestPrepareAndPoll:
    def _strategy(self, workdir, components):
        path = workdir / "artifacts/strat-tasks/RHAISTRAT-9001.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\n" + yaml.safe_dump(
            {"strat_id": "RHAISTRAT-9001", "title": "T",
             "components": components}) + "---\n\nAdd a dashboard page.\n")

    def test_prepare_writes_request_and_clears_stale_result(self, workdir):
        self._strategy(workdir, ["AI Core Dashboard"])
        stale = workdir / "artifacts/repo-resolution/RHAISTRAT-9001-result.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}")
        path = repository_resolution.prepare_request("RHAISTRAT-9001")
        assert json.loads((workdir / path).read_text()) == {
            "description": "Add a dashboard page.",
            "components": ["AI Core Dashboard"]}
        assert not stale.exists()

    def test_prepare_without_components(self, workdir):
        self._strategy(workdir, [])
        path = repository_resolution.prepare_request("RHAISTRAT-9001")
        assert json.loads((workdir / path).read_text())["components"] == []

    def test_poll_completes_when_result_exists(self, workdir):
        assert check_id("resolve", "RHAISTRAT-9001") == "pending"
        result = workdir / "artifacts/repo-resolution/RHAISTRAT-9001-result.json"
        result.parent.mkdir(parents=True)
        result.write_text("{}")
        assert check_id("resolve", "RHAISTRAT-9001") == "completed"


class TestDecomposeVars:
    def _next_action(self, workdir, repo_mode, capsys):
        (workdir / "tmp/pipeline-active-ids.txt").write_text("RHAISTRAT-9001\n")
        state = {"phase": "DECOMPOSE", "batch_size": 25, "repository_mode": repo_mode}
        with open("tmp/pipeline-state.yaml", "w") as f:
            yaml.safe_dump(state, f)
        pipeline_state.cmd_next_action([])
        out = yaml.safe_load(capsys.readouterr().out)
        return out["agents"][0]["vars"]

    def test_flag_on_passes_repository_vars(self, workdir, capsys):
        variables = self._next_action(workdir, True, capsys)
        assert "REPOSITORY_MODE=true" in variables
        assert "RESOLUTION=artifacts/repo-resolution/RHAISTRAT-9001-result.json" \
            in variables

    def test_flag_off_passes_legacy_vars_only(self, workdir, capsys):
        assert self._next_action(workdir, False, capsys) == "ID=RHAISTRAT-9001\n"


class TestEpicFrontmatter:
    def _epic(self, workdir, **fields):
        from artifact_utils import read_frontmatter_validated
        path = workdir / "artifacts/epic-tasks/RHAISTRAT-9001-E001.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"epic_id": "RHAISTRAT-9001-E001", "title": "T",
                "parent_strat": "RHAISTRAT-9001", "component": "C", "team": "T",
                "type": "Implementation", "priority": "P0", **fields}
        path.write_text("---\n" + yaml.safe_dump(data) + "---\nbody\n")
        return read_frontmatter_validated(str(path), "epic-task")[0]

    def test_resolved_and_unresolved_fields_validate(self, workdir):
        data = self._epic(workdir, repository_key="odh-dashboard",
                          repository_resolution=
                          "artifacts/repo-resolution/RHAISTRAT-9001-result.json")
        assert data["repository_key"] == "odh-dashboard"
        data = self._epic(workdir, repository_key=None, repository_resolution=
                          "artifacts/repo-resolution/RHAISTRAT-9001-E002-result.json")
        assert data["repository_key"] is None

    def test_legacy_epic_has_no_repository(self, workdir):
        data = self._epic(workdir)
        assert data["repository_key"] is None and data["repository_resolution"] is None

    def test_resolution_path_outside_resolution_dir_is_rejected(self, workdir):
        from artifact_utils import ValidationError
        with pytest.raises(ValidationError):
            self._epic(workdir, repository_resolution="/etc/passwd")


class TestCheckEpics:
    """check-epics: every epic's repository matches a verified result."""

    @pytest.fixture
    def env(self, workdir):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        from test_resolution_lookup import _snapshot
        from test_resolution_verify import DASHBOARD_DOC, DASHBOARD_TEXT
        manifest, root = _snapshot(workdir / "src")
        (root / "architecture" / DASHBOARD_DOC).write_text(DASHBOARD_TEXT)
        res = workdir / "artifacts/repo-resolution"
        res.mkdir(parents=True)
        (workdir / "artifacts/epic-tasks").mkdir(parents=True)
        quote = {"source": "architecture", "path": DASHBOARD_DOC,
                 "quote": "The ODH Dashboard is the web UI for OpenShift AI"}
        self._write_pair(res, "RHAISTRAT-9001", {
            "status": "resolved", "phase1": {"skipped": "no-components"},
            "repositories": [{"key": "odh-dashboard",
                              "namespace": "red-hat-data-services",
                              "name": "odh-dashboard",
                              "url": "https://github.com/red-hat-data-services/odh-dashboard",
                              "hint": "none", "evidence": [quote]}],
            "overridden_hints": []})
        self._write_pair(res, "RHAISTRAT-9001-E002", {
            "status": "unresolved", "phase1": {"skipped": "no-components"},
            "reason": "No architecture document describes this work.",
            "candidates": [], "evidence": []})
        return str(root / "manifest.json")

    def _write_pair(self, res, stem, result):
        (res / f"{stem}-request.json").write_text(
            json.dumps({"description": "work", "components": []}))
        (res / f"{stem}-result.json").write_text(json.dumps(result))

    def _epic(self, workdir, num, **fields):
        path = workdir / f"artifacts/epic-tasks/RHAISTRAT-9001-E{num:03d}.md"
        path.write_text("---\n" + yaml.safe_dump(
            {"epic_id": f"RHAISTRAT-9001-E{num:03d}", **fields}) + "---\nbody\n")

    def test_consistent_epics_pass(self, workdir, env):
        self._epic(workdir, 1, repository_key="odh-dashboard", repository_resolution=
                   "artifacts/repo-resolution/RHAISTRAT-9001-result.json")
        self._epic(workdir, 2, repository_key=None, repository_resolution=
                   "artifacts/repo-resolution/RHAISTRAT-9001-E002-result.json")
        assert repository_resolution.check_epics("RHAISTRAT-9001", env) == []

    def test_key_not_in_result_is_reported(self, workdir, env):
        self._epic(workdir, 1, repository_key="kserve", repository_resolution=
                   "artifacts/repo-resolution/RHAISTRAT-9001-result.json")
        [error] = repository_resolution.check_epics("RHAISTRAT-9001", env)
        assert "'kserve' is not in" in error

    def test_missing_resolution_is_reported(self, workdir, env):
        self._epic(workdir, 1, repository_key="odh-dashboard")
        [error] = repository_resolution.check_epics("RHAISTRAT-9001", env)
        assert "repository_resolution is not set" in error

    def test_key_on_unresolved_result_is_reported(self, workdir, env):
        self._epic(workdir, 2, repository_key="odh-dashboard", repository_resolution=
                   "artifacts/repo-resolution/RHAISTRAT-9001-E002-result.json")
        [error] = repository_resolution.check_epics("RHAISTRAT-9001", env)
        assert "is unresolved" in error

    def test_tampered_result_is_reported(self, workdir, env):
        path = workdir / "artifacts/repo-resolution/RHAISTRAT-9001-result.json"
        result = json.loads(path.read_text())
        result["repositories"][0]["evidence"][0]["quote"] = "invented text"
        path.write_text(json.dumps(result))
        self._epic(workdir, 1, repository_key="odh-dashboard", repository_resolution=
                   "artifacts/repo-resolution/RHAISTRAT-9001-result.json")
        errors = repository_resolution.check_epics("RHAISTRAT-9001", env)
        assert any("quote not found verbatim" in e for e in errors)

    def test_no_epics_is_reported(self, workdir, env):
        assert repository_resolution.check_epics("RHAISTRAT-9001", env) == \
            ["RHAISTRAT-9001: no epic files found"]

    def test_review_and_revise_get_repository_vars(self):
        for phase in ("REVIEW_DECOMP", "REVISE_DECOMP", "RE_REVIEW", "RE_REVISE"):
            assert PHASE_CONFIG[phase]["repository_vars"]["REPOSITORY_MODE"] == "true"
