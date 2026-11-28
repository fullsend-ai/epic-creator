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
        monkeypatch.setattr(repository_resolution, "ensure_sources", fail)
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
        monkeypatch.setattr(repository_resolution, "ensure_sources",
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
        monkeypatch.setattr(repository_resolution, "ensure_sources", fail)
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


class TestEnsureSources:
    def _loaded(self, workdir, config_text):
        import hashlib
        config = workdir / "resolver.yaml"
        config.write_text(config_text)
        out = workdir / "sources"
        (out / "architecture").mkdir(parents=True)
        (out / "manifest.json").write_text(json.dumps({
            "config_digest": hashlib.sha256(config_text.encode()).hexdigest(),
            "sources": [{"id": "architecture", "snapshot": "architecture"}]}))
        return str(config), str(out)

    def test_reuses_manifest_loaded_for_same_config(self, workdir, monkeypatch):
        config, out = self._loaded(workdir, "version: 1\n")
        monkeypatch.setattr(repository_resolution, "load_sources",
                            lambda *a, **k: pytest.fail("must reuse"))
        assert repository_resolution.ensure_sources(config, out) == \
            f"{out}/manifest.json"

    def test_reloads_when_config_changed(self, workdir, monkeypatch):
        config, out = self._loaded(workdir, "version: 1\n")
        (workdir / "resolver.yaml").write_text("version: 2\n")
        calls = []
        monkeypatch.setattr(repository_resolution, "load_sources",
                            lambda c, o: calls.append(c) or f"{o}/manifest.json")
        repository_resolution.ensure_sources(config, out)
        assert calls == [config]

    def test_loads_when_nothing_loaded(self, workdir, monkeypatch):
        calls = []
        monkeypatch.setattr(repository_resolution, "load_sources",
                            lambda c, o: calls.append(o) or f"{o}/manifest.json")
        repository_resolution.ensure_sources("resolver.yaml", "nowhere")
        assert calls == ["nowhere"]


class TestHarnessWiring:
    ROOT = os.path.join(os.path.dirname(__file__), "..")

    def test_sandbox_receives_flag_through_env_file(self):
        with open(os.path.join(self.ROOT, ".fullsend/epic-creator/epic-creator.yaml")) as f:
            harness = yaml.safe_load(f)
        entry = next(h for h in harness["host_files"]
                     if h["src"] == "epic-creator/repository-mode.env")
        assert entry["expand"] is True
        assert entry["dest"].startswith("/sandbox/workspace/.env.d/")
        with open(os.path.join(self.ROOT, ".fullsend/epic-creator/repository-mode.env")) as f:
            assert "EPIC_CREATOR_REPOSITORY_MODE=${EPIC_CREATOR_REPOSITORY_MODE}" in f.read()

    def _pre_script(self, workdir, flag):
        import shutil
        import subprocess
        repo = workdir / "repo"
        (repo / "scripts").mkdir(parents=True)
        for name in ("fetch-architecture-context.sh", "fetch_components.py"):
            (repo / "scripts" / name).write_text("")
        log = workdir / "calls.log"
        (repo / "scripts" / "repository_resolution.py").write_text(
            "import os, sys\n"
            f"open({str(log)!r}, 'a').write(sys.argv[1] + '\\n')\n"
            "v = os.environ.get('EPIC_CREATOR_REPOSITORY_MODE', '')\n"
            "if sys.argv[1] == 'flag':\n"
            "    if v not in ('', 'true', 'false'): sys.exit(2)\n"
            "    print('on' if v == 'true' else 'off')\n")
        env = dict(os.environ, TARGET_REPO_DIR=str(repo))
        env.pop("EPIC_CREATOR_REPOSITORY_MODE", None)
        if flag is not None:
            env["EPIC_CREATOR_REPOSITORY_MODE"] = flag
        script = os.path.join(self.ROOT, ".fullsend/epic-creator/pre-epic-creator.sh")
        result = subprocess.run(["bash", script], env=env, capture_output=True, text=True)
        calls = log.read_text().split() if log.exists() else []
        return result.returncode, calls

    def test_pre_script_loads_sources_only_when_on(self, workdir):
        assert self._pre_script(workdir, "true") == (0, ["flag", "load"])

    @pytest.mark.parametrize("flag", [None, ""])
    def test_pre_script_untouched_when_unset(self, workdir, flag):
        assert self._pre_script(workdir, flag) == (0, [])

    def test_pre_script_skips_loading_when_false(self, workdir):
        assert self._pre_script(workdir, "false") == (0, ["flag"])

    def test_pre_script_stops_on_malformed_flag(self, workdir):
        code, calls = self._pre_script(workdir, "yes")
        assert code != 0 and calls == ["flag"]
