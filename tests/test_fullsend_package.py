import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
FULLSEND = ROOT / ".fullsend"
PACKAGE_CONFIG = yaml.safe_load(
    (FULLSEND / "config.yaml").read_text(encoding="utf-8")
)
EPIC_AGENT = next(
    agent for agent in PACKAGE_CONFIG["agents"]
    if agent.get("name") == "epic-creator"
    or Path(agent.get("source", "")).stem == "epic-creator"
)
HARNESS = yaml.safe_load(
    (FULLSEND / EPIC_AGENT["source"]).read_text(encoding="utf-8")
)
SCHEMA = FULLSEND / HARNESS["validation_loop"]["schema"]
VALIDATOR = FULLSEND / HARNESS["validation_loop"]["script"]
PRE = FULLSEND / HARNESS["pre_script"]
POST = FULLSEND / HARNESS["post_script"]
RUN_START = "2026-10-20T00:00:00Z"
RUN_REPORT = Path(f"artifacts/decompose-runs/{RUN_START.replace(':', '-')}.yaml")


def _environment(tmp_path, **extra):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "LANG": "C",
    }
    env.update({key: str(value) for key, value in extra.items()})
    return env


def _fixture(iteration, *, phase="DONE", report=True):
    output = iteration / "output"
    output.mkdir(parents=True)
    (output / "agent-result.json").write_text(
        json.dumps({
            "action": "completed",
            "pipeline": "epic-decompose",
            "summary": "Synthetic completed run",
            "errors": [],
        }) + "\n",
        encoding="utf-8",
    )
    repo = iteration.parent / "source-repo"
    (repo / "tmp").mkdir(parents=True)
    (repo / "tmp" / "pipeline-state.yaml").write_text(
        yaml.safe_dump({"phase": phase, "start_time": RUN_START}), encoding="utf-8"
    )
    runs = repo / "artifacts" / "decompose-runs"
    runs.mkdir(parents=True)
    if report:
        (repo / RUN_REPORT).write_text(
            "review:\n  pass: false\n", encoding="utf-8"
        )
    return output, repo


def _run_validator(iteration, repo=None, **extra):
    values = {"FULLSEND_OUTPUT_SCHEMA": SCHEMA, **extra}
    if repo is not None:
        values["TARGET_REPO_DIR"] = repo
    return subprocess.run(
        ["bash", str(VALIDATOR)],
        cwd=iteration,
        env=_environment(iteration, **values),
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_package_configuration():
    sandbox_env = HARNESS["env"]["sandbox"]
    runner_env = HARNESS["env"]["runner"]
    assert HARNESS["model"] == sandbox_env["CLAUDE_CODE_SUBAGENT_MODEL"]
    assert runner_env["JIRA_TOKEN"] == "${JIRA_TOKEN}"
    assert "JIRA_TOKEN" not in sandbox_env
    profiles = [
        yaml.safe_load((FULLSEND / path).read_text(encoding="utf-8"))
        for path in HARNESS["openshell"]["profiles"]
    ]
    jira_profile = next(
        profile for profile in profiles
        if any(
            "JIRA_TOKEN" in credential.get("env_vars", [])
            for credential in profile.get("credentials", [])
        )
    )
    credential = next(
        credential for credential in jira_profile["credentials"]
        if "JIRA_TOKEN" in credential.get("env_vars", [])
    )
    assert credential["required"] is True
    assert jira_profile["endpoints"]
    assert all(
        endpoint["access"] == "read-only"
        and endpoint["enforcement"] == "enforce"
        for endpoint in jira_profile["endpoints"]
    )

    providers = [
        yaml.safe_load((FULLSEND / path).read_text(encoding="utf-8"))
        for path in HARNESS["providers"]
        if (FULLSEND / path).is_file()
    ]
    jira_provider = next(
        provider for provider in providers
        if provider.get("type") == jira_profile["id"]
    )
    assert "JIRA_TOKEN" in jira_provider["credentials"]


def test_validator_accepts_completed_run_with_failed_business_review(tmp_path):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    result = _run_validator(iteration, repo)
    assert result.returncode == 0, result.stderr + result.stdout
    assert sorted(path.name for path in output.iterdir()) == ["agent-result.json"]


@pytest.mark.parametrize("case", ["older-report", "dotfile", "empty-current-report"])
def test_validator_requires_nonempty_current_run_report(tmp_path, case):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration, report=False)
    runs = repo / "artifacts/decompose-runs"
    if case == "older-report":
        (runs / "2026-10-19T00-00-00Z.yaml").write_text("previous run\n")
    elif case == "dotfile":
        (runs / ".placeholder").write_text("not a report\n")
    else:
        (repo / RUN_REPORT).touch()
    result = _run_validator(iteration, repo)
    assert result.returncode != 0


@pytest.mark.parametrize("start_time", [
    None, "", "not-a-timestamp", "../../outside", 42,
    "2026-02-30T00:00:00Z", "2026-10-20T00:00:00+00:00",
])
def test_validator_rejects_missing_or_invalid_run_start(tmp_path, start_time):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration)
    state = {"phase": "DONE"}
    if start_time is not None:
        state["start_time"] = start_time
    (repo / "tmp/pipeline-state.yaml").write_text(yaml.safe_dump(state))
    result = _run_validator(iteration, repo)
    assert result.returncode != 0


@pytest.mark.parametrize("value", [f"'{RUN_START}", f'{RUN_START}"', f"'{RUN_START}\""])
def test_validator_rejects_unpaired_timestamp_quotes(tmp_path, value):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration)
    (repo / "tmp/pipeline-state.yaml").write_text(f"phase: DONE\nstart_time: {value}\n")
    assert _run_validator(iteration, repo).returncode != 0


def test_validator_rejects_duplicate_run_start(tmp_path):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration)
    (repo / "tmp/pipeline-state.yaml").write_text(
        f"phase: DONE\nstart_time: '{RUN_START}'\nstart_time: '{RUN_START}'\n"
    )
    assert _run_validator(iteration, repo).returncode != 0


def test_validator_rejects_invalid_result_json(tmp_path):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    (output / "agent-result.json").write_text("{not json\n", encoding="utf-8")
    result = _run_validator(iteration, repo)
    assert result.returncode != 0


@pytest.mark.parametrize(("field", "value"), [
    ("pipeline", "wrong"),
    ("action", "failed"),
])
def test_validator_rejects_wrong_result_contract(tmp_path, field, value):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    result_file = output / "agent-result.json"
    data = json.loads(result_file.read_text(encoding="utf-8"))
    data[field] = value
    result_file.write_text(json.dumps(data) + "\n", encoding="utf-8")
    result = _run_validator(iteration, repo)
    assert result.returncode != 0


@pytest.mark.parametrize("missing", ["result-file", "output-directory", "pipeline-state"])
def test_validator_rejects_missing_run_files_or_directories(tmp_path, missing):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    if missing == "result-file":
        (output / "agent-result.json").unlink()
    elif missing == "output-directory":
        shutil.rmtree(output)
    else:
        (repo / "tmp" / "pipeline-state.yaml").unlink()
    result = _run_validator(iteration, repo)
    assert result.returncode != 0


def test_validator_rejects_missing_target_repository(tmp_path):
    iteration = tmp_path / "iteration-1"
    _fixture(iteration)
    result = _run_validator(iteration)
    assert result.returncode != 0


def test_validator_rejects_unfinished_run_phase(tmp_path):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration, phase="DECOMPOSE")
    result = _run_validator(iteration, repo)
    assert result.returncode != 0


def test_validator_rejects_empty_report_directory(tmp_path):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration, report=False)
    result = _run_validator(iteration, repo)
    assert result.returncode != 0


@pytest.mark.parametrize("unsafe_kind", ["symlink", "fifo"])
def test_validator_rejects_unsafe_result_file_without_blocking(tmp_path, unsafe_kind):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    result_file = output / "agent-result.json"
    result_file.unlink()
    if unsafe_kind == "symlink":
        private_file = tmp_path / "private.json"
        private_file.write_text("{}\n", encoding="utf-8")
        result_file.symlink_to(private_file)
    else:
        os.mkfifo(result_file)
    assert _run_validator(iteration, repo).returncode != 0


@pytest.mark.parametrize("unsafe_kind", ["symlink", "fifo"])
@pytest.mark.parametrize("payload_dir", ["artifacts", "tmp"])
def test_validator_rejects_unsafe_payload_entries(tmp_path, unsafe_kind, payload_dir):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration)
    directory = repo / payload_dir
    if unsafe_kind == "symlink":
        private_file = tmp_path / "private-payload"
        private_file.write_text("private\n", encoding="utf-8")
        (directory / "untrusted").symlink_to(private_file)
    else:
        os.mkfifo(directory / "untrusted")
    assert _run_validator(iteration, repo).returncode != 0


def _post(iteration, output, repo, target, *, path=None):
    env = _environment(
        iteration,
        FULLSEND_VALIDATED_ITERATION_DIR=output,
        REPO_DIR=repo,
        TARGET_REPO_DIR=target,
    )
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        ["bash", str(POST)], cwd=iteration, env=env,
        capture_output=True, text=True, timeout=10,
    )


def _disposable_target(path):
    path.mkdir()
    (path / "keep.txt").write_text("preserve\n", encoding="utf-8")
    (path / "artifacts").mkdir()
    (path / "artifacts" / "old.txt").write_text("replace\n", encoding="utf-8")
    (path / "tmp").mkdir()
    (path / "tmp" / "old.txt").write_text("replace\n", encoding="utf-8")
    return path


def test_post_copies_validated_repository_trees_to_disposable_target(tmp_path):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    assert _run_validator(iteration, repo).returncode == 0
    target = _disposable_target(tmp_path / "disposable")
    result = _post(iteration, output, repo, target)
    assert result.returncode == 0, result.stderr + result.stdout
    report = RUN_REPORT
    state = Path("tmp/pipeline-state.yaml")
    for relative in (report, state):
        assert (target / relative).read_bytes() == (repo / relative).read_bytes()
    assert not (target / "artifacts" / "old.txt").exists()
    assert not (target / "tmp" / "old.txt").exists()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "preserve\n"
    assert not (target / "agent-result.json").exists()
    assert sorted(path.name for path in output.iterdir()) == ["agent-result.json"]
    assert (repo / report).is_file()


def test_post_copy_failure_propagates_without_claiming_rollback(tmp_path):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    assert _run_validator(iteration, repo).returncode == 0
    target = _disposable_target(tmp_path / "disposable")
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    stub = stub_dir / "cp"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    result = _post(
        iteration, output, repo, target,
        path=f"{stub_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
    )
    assert result.returncode != 0
    assert (target / "keep.txt").read_text(encoding="utf-8") == "preserve\n"
    assert (repo / RUN_REPORT).is_file()


def test_post_removal_failure_stops_copyback(tmp_path):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    target = _disposable_target(tmp_path / "target")
    result = subprocess.run(
        ["bash", "-c", 'rm() { return 1; }; source "$1"', "_", str(POST)],
        cwd=iteration,
        env=_environment(
            tmp_path, FULLSEND_VALIDATED_ITERATION_DIR=output,
            REPO_DIR=repo, TARGET_REPO_DIR=target,
        ),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert (target / "tmp/old.txt").read_text() == "replace\n"
    assert (target / "artifacts/old.txt").read_text() == "replace\n"
    assert not (target / RUN_REPORT).exists()


@pytest.mark.parametrize("variable", [
    "TARGET_REPO_DIR", "FULLSEND_VALIDATED_ITERATION_DIR", "REPO_DIR",
])
@pytest.mark.parametrize("empty", [False, True])
def test_post_rejects_missing_or_empty_required_paths(tmp_path, variable, empty):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    target = _disposable_target(tmp_path / "target")
    env = _environment(
        tmp_path, FULLSEND_VALIDATED_ITERATION_DIR=output,
        REPO_DIR=repo, TARGET_REPO_DIR=target,
    )
    if empty:
        env[variable] = ""
    else:
        env.pop(variable)
    result = subprocess.run(
        ["bash", "-c", 'rm() { echo DELETE_CALLED >&2; return 99; }; source "$1"',
         "_", str(POST)],
        cwd=iteration, env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert variable in result.stderr
    assert "DELETE_CALLED" not in result.stderr
    assert (target / "tmp/old.txt").is_file()
    assert (target / "artifacts/old.txt").is_file()


@pytest.mark.parametrize(
    ("skip_flags", "expected_env"),
    [({}, "unset unset"), ({
        "EPIC_SKIP_BOOTSTRAP": "1",
        "EPIC_SKIP_COMPONENT_FETCH": "1",
    }, "1 1")],
)
def test_pre_runs_only_target_bootstrap_scripts(tmp_path, skip_flags, expected_env):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "fetch-architecture-context.sh").write_text(
        "#!/usr/bin/env bash\n"
        "touch architecture-called\n"
        "printf '%s %s\\n' \"${EPIC_SKIP_BOOTSTRAP-unset}\" "
        "\"${EPIC_SKIP_COMPONENT_FETCH-unset}\" > architecture-env\n",
        encoding="utf-8",
    )
    (scripts / "fetch_components.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path('components-called').touch()\n"
        "Path('components-env').write_text(\n"
        "    f\"{os.environ.get('EPIC_SKIP_BOOTSTRAP', 'unset')} \"\n"
        "    f\"{os.environ.get('EPIC_SKIP_COMPONENT_FETCH', 'unset')}\")\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(PRE)], cwd=tmp_path,
        env=_environment(tmp_path, TARGET_REPO_DIR=checkout, **skip_flags),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert (checkout / "architecture-called").is_file()
    assert (checkout / "components-called").is_file()
    assert (checkout / "architecture-env").read_text().strip() == expected_env
    assert (checkout / "components-env").read_text() == expected_env
    missing = subprocess.run(
        ["bash", str(PRE)], cwd=tmp_path, env=_environment(tmp_path),
        capture_output=True, text=True, timeout=10,
    )
    assert missing.returncode != 0


def test_sandbox_bootstrap_reuses_prepared_inputs(tmp_path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "fetch-architecture-context.sh", "fetch_components.py", "jira_utils.py",
    ):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)

    context = checkout / ".context"
    architecture = context / "architecture-context"
    architecture.mkdir(parents=True)
    architecture_sentinel = architecture / "sentinel.txt"
    architecture_sentinel.write_text("prepared architecture\n", encoding="utf-8")
    components = context / "rhai-components.txt"
    components.write_text("Prepared Component\n", encoding="utf-8")

    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    network_attempts = tmp_path / "network-attempts"
    for name in ("curl", "git"):
        stub = stub_dir / name
        stub.write_text(
            "#!/bin/sh\n"
            "printf '%s %s\\n' \"$0\" \"$*\" >> \"$NETWORK_ATTEMPTS\"\n"
            "exit 99\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

    sandbox_env = {key: str(value) for key, value in HARNESS["env"]["sandbox"].items()}
    sandbox_env.pop("JIRA_TOKEN", None)
    sandbox_env["PATH"] = os.pathsep.join((str(stub_dir), os.environ.get("PATH", "")))
    sandbox_env["NETWORK_ATTEMPTS"] = str(network_attempts)
    env = _environment(tmp_path, **sandbox_env)

    architecture_result = subprocess.run(
        ["bash", str(scripts / "fetch-architecture-context.sh")],
        cwd=checkout, env=env, capture_output=True, text=True, timeout=10,
    )
    component_result = subprocess.run(
        [sys.executable, str(scripts / "fetch_components.py")],
        cwd=checkout, env=env, capture_output=True, text=True, timeout=10,
    )

    assert architecture_result.returncode == 0, architecture_result.stderr
    assert component_result.returncode == 0, component_result.stderr
    assert architecture_sentinel.read_text(encoding="utf-8") == "prepared architecture\n"
    assert components.read_text(encoding="utf-8") == "Prepared Component\n"
    assert not network_attempts.exists()


def test_repository_skill_layout():
    repo = ROOT.resolve()
    skills = [
        ROOT / ".agents/skills/epic-decompose",
        ROOT / "skills/epic-decompose",
        ROOT / ".claude/skills/epic-decompose",
    ]
    for relative in (
        "SKILL.md",
        "prompts/fetch-agent.md",
        "prompts/decompose-agent.md",
        "prompts/review-agent.md",
        "prompts/revise-agent.md",
    ):
        assets = [(skill / relative).resolve() for skill in skills]
        assert all(repo in asset.parents for asset in assets)
        assert len({asset.read_bytes() for asset in assets}) == 1


def _compact_hook_commands(path):
    config = json.loads(path.read_text())
    return [
        hook["command"]
        for entry in config["hooks"]["SessionStart"]
        if entry.get("matcher") == "compact"
        for hook in entry["hooks"]
        if hook.get("type") == "command"
    ]


def test_plugin_manifest_name():
    manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "epic-creator"


def _hook_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/pipeline_state.py", scripts / "pipeline_state.py")
    state = checkout / "tmp/pipeline-state.yaml"
    state.parent.mkdir()
    state.write_text("phase: DECOMPOSE\n")
    return checkout


def _run_hook(command, cwd, tmp_path, **extra_env):
    return subprocess.run(
        ["bash", "-c", command],
        cwd=cwd,
        env=_environment(tmp_path, **extra_env),
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    ("role", "nested", "expected_phase"),
    [
        (HARNESS["role"], False, True),
        (HARNESS["role"], True, True),
        (f"{HARNESS['role']}-other", False, False),
        (None, False, False),
    ],
)
def test_project_compact_hook_activation(tmp_path, role, nested, expected_phase):
    checkout = _hook_checkout(tmp_path)
    cwd = checkout / "nested" if nested else checkout
    if nested:
        cwd.mkdir()
    command = "\n".join(_compact_hook_commands(ROOT / ".claude/settings.json"))
    env = {
        "EPIC_CREATOR_ENABLE_CONTEXT_HOOK": HARNESS["env"]["sandbox"][
            "EPIC_CREATOR_ENABLE_CONTEXT_HOOK"
        ],
        "CLAUDE_PROJECT_DIR": str(checkout),
    }
    if role is not None:
        env["FULLSEND_ROLE"] = role
    result = _run_hook(command, cwd, tmp_path, **env)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    if expected_phase:
        assert "DECOMPOSE" in result.stdout
    else:
        assert result.stdout == ""


def test_project_hook_does_not_duplicate_plugin_hook(tmp_path):
    checkout = _hook_checkout(tmp_path)
    project_command = "\n".join(_compact_hook_commands(ROOT / ".claude/settings.json"))
    plugin_command = "\n".join(_compact_hook_commands(ROOT / "hooks/hooks.json"))
    env = {
        "EPIC_CREATOR_ENABLE_CONTEXT_HOOK": HARNESS["env"]["sandbox"][
            "EPIC_CREATOR_ENABLE_CONTEXT_HOOK"
        ],
        "CLAUDE_PROJECT_DIR": str(checkout),
        "CLAUDE_PLUGIN_ROOT": str(checkout),
    }

    project_result = _run_hook(project_command, checkout, tmp_path, **env)
    assert project_result.returncode == 0, project_result.stderr
    assert project_result.stderr == ""
    assert project_result.stdout == ""

    plugin_result = _run_hook(plugin_command, checkout, tmp_path, **env)
    assert plugin_result.returncode == 0, plugin_result.stderr
    assert plugin_result.stderr == ""
    assert "DECOMPOSE" in plugin_result.stdout
