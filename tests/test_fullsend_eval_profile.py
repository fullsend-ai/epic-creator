#!/usr/bin/env python3
"""Pin the Fullsend eval profile to the Claude Code baseline."""

import copy
import json
import textwrap
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parent.parent
BASE = REPO / "eval" / "epic-decompose.yaml"
PROFILE = REPO / "eval" / "profiles" / "fullsend.yaml"


class ReplaceList(list):
    pass


class ProfileLoader(yaml.SafeLoader):
    pass


def _replace(loader, node):
    if not isinstance(node, yaml.SequenceNode):
        raise yaml.constructor.ConstructorError(
            None, None, "!replace applies to list values only", node.start_mark
        )
    return ReplaceList(loader.construct_sequence(node, deep=True))


ProfileLoader.add_constructor("!replace", _replace)


def _load(path, loader=yaml.SafeLoader):
    with open(path) as stream:
        return yaml.load(stream, Loader=loader)


def _strip_replace(value):
    if isinstance(value, ReplaceList):
        return [_strip_replace(item) for item in value]
    if isinstance(value, list):
        return [_strip_replace(item) for item in value]
    if isinstance(value, dict):
        return {key: _strip_replace(item) for key, item in value.items()}
    return value


def _merge(base, profile):
    merged = copy.deepcopy(base)
    for key, value in profile.items():
        if key == "extends":
            continue
        current = merged.get(key)
        if isinstance(value, ReplaceList):
            merged[key] = _strip_replace(value)
        elif isinstance(value, dict) and isinstance(current, dict):
            merged[key] = _merge(current, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _diff(left, right, prefix=""):
    if isinstance(left, dict) and isinstance(right, dict):
        paths = set()
        for key in set(left) | set(right):
            paths |= _diff(
                left.get(key), right.get(key), f"{prefix}{key}."
            )
        return paths
    return set() if left == right else {prefix.rstrip(".")}


def _run_dispatcher_turn_discipline(stdout_events):
    judges = _load(BASE)["judges"]
    check = next(
        j["check"] for j in judges if j["name"] == "dispatcher_turn_discipline"
    )
    namespace = {}
    exec(
        "def judge(outputs):\n" + textwrap.indent(check, "    "),
        namespace,
    )
    stdout = "\n".join(json.dumps(event) for event in stdout_events)
    return namespace["judge"]({"stdout": stdout})


def _init_event():
    return {"type": "system", "subtype": "init"}


def _tool_event(name, tool_input):
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": name, "input": tool_input}]
        },
    }


def test_profile_changes_only_runner_and_tool_interception():
    base = _load(BASE)
    profile = _load(PROFILE, ProfileLoader)
    merged = _merge(base, profile)

    assert _diff(base, merged) == {
        "runner.type",
        "runner.agent",
        "runner.extra_args",
        "execution.env.JIRA_USER",
        "execution.env.JIRA_TOKEN",
        "inputs.tools",
    }


def test_profile_selects_eval_harness_and_removes_interceptor():
    profile = _load(PROFILE, ProfileLoader)

    assert set(profile) == {"extends", "runner", "execution", "inputs"}
    assert profile["runner"] == {
        "type": "fullsend",
        "agent": "epic-creator-eval",
        "extra_args": ["--forge", "github"],
    }
    assert "fullsend_dir" not in profile["runner"]
    assert profile["execution"]["env"] == {"JIRA_USER": "", "JIRA_TOKEN": ""}
    assert profile["inputs"]["tools"] == []


def test_baseline_remains_claude_code_with_interceptor():
    base = _load(BASE)

    assert base["runner"]["type"] == "claude-code"
    assert base["runner"].get("agent") is None
    assert len(base["inputs"]["tools"]) == 1


def test_profile_extends_the_baseline_from_its_own_directory():
    profile = _load(PROFILE, ProfileLoader)
    base_path = (PROFILE.parent / profile["extends"]).resolve()

    assert base_path == BASE.resolve()


def test_default_fullsend_dir_resolves_to_the_repo_package():
    merged = _merge(_load(BASE), _load(PROFILE, ProfileLoader))
    fullsend_dir = Path(merged["runner"].get("fullsend_dir", ".fullsend"))
    if not fullsend_dir.is_absolute():
        fullsend_dir = REPO / fullsend_dir
    assert fullsend_dir.resolve() == (REPO / ".fullsend").resolve()


def test_profile_keeps_the_baseline_measuring_stick():
    base = _load(BASE)
    merged = _merge(base, _load(PROFILE, ProfileLoader))
    for key in ("models", "dataset", "outputs", "judges", "thresholds"):
        assert merged[key] == base[key], key


def test_profile_leaves_timeout_and_validation_loop_untouched():
    base = _load(BASE)
    merged = _merge(base, _load(PROFILE, ProfileLoader))
    assert merged["execution"]["timeout"] == base["execution"]["timeout"]
    assert "symlinks" not in merged["execution"]


def test_dispatcher_turn_discipline_allows_output_only_post_done_retry():
    passed, rationale = _run_dispatcher_turn_discipline(
        [
            _init_event(),
            _tool_event(
                "Write",
                {"file_path": "agent-result.json", "content": "{}"},
            ),
            _init_event(),
            _tool_event(
                "Bash",
                {"command": "fullsend-check-output agent-result.json"},
            ),
        ]
    )

    assert passed
    assert "output-only recovery resume" in rationale


def test_dispatcher_turn_discipline_fails_resumed_pipeline_next_action():
    passed, _ = _run_dispatcher_turn_discipline(
        [
            _init_event(),
            _init_event(),
            _tool_event(
                "Bash",
                {"command": "python3 scripts/pipeline_state.py next-action"},
            ),
        ]
    )

    assert not passed


def test_dispatcher_turn_discipline_fails_resumed_pipeline_wait_for_wave():
    passed, _ = _run_dispatcher_turn_discipline(
        [
            _init_event(),
            _init_event(),
            _tool_event(
                "Bash",
                {"command": "python3 scripts/pipeline_state.py wait-for-wave"},
            ),
        ]
    )

    assert not passed


def test_dispatcher_turn_discipline_fails_resumed_agent_launch():
    passed, _ = _run_dispatcher_turn_discipline(
        [
            _init_event(),
            _init_event(),
            _tool_event("Agent", {"prompt": "resume pipeline work"}),
        ]
    )

    assert not passed
