#!/usr/bin/env python3
"""Pipeline state machine for the thin dispatcher.

Phase tracking, config, and transition logic for epic-decompose.

Usage:
    python3 scripts/pipeline_state.py init [--batch-size N] [--headless]
    python3 scripts/pipeline_state.py get-phase
    python3 scripts/pipeline_state.py set-phase <PHASE>
    python3 scripts/pipeline_state.py get-phase-config
    python3 scripts/pipeline_state.py run-phase
    python3 scripts/pipeline_state.py advance [--dry-run]
    python3 scripts/pipeline_state.py set-wave <IDs>
    python3 scripts/pipeline_state.py set key=value ...
    python3 scripts/pipeline_state.py get <key>
    python3 scripts/pipeline_state.py status
    python3 scripts/pipeline_state.py diagnose
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import yaml
from pipeline_workload import (BOUND_FIELDS, bound_workload, phase_config,
                               validate_ids, validate_completed, validate_inputs)
from pipeline_status import (all_statuses, clear_all_statuses, clear_status,
                             read_status, write_status)
from workload import WorkloadError, get_workload

STATE_FILE = "tmp/pipeline-state.yaml"
WAVE_IDS_FILE = "tmp/pipeline-wave-ids.txt"
DISPATCH_MARKER = "tmp/.dispatch-marker"

MAX_NEXT_ACTION_ITERATIONS = 50


# ---------- YAML block-scalar dumper (scoped) ----------

def _str_representer(dumper, data):
    if '\n' in data:
        return dumper.represent_scalar(
            'tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


class _BlockDumper(yaml.Dumper):
    pass


_BlockDumper.add_representer(str, _str_representer)

# ---------- Phase enum ----------

PHASES = [
    "BATCH_START", "FETCH", "DECOMPOSE", "REVIEW_DECOMP",
    "REVISE_DECOMP",
    "RE_REVIEW_CHECK", "RE_REVIEW", "REVISE_CHECK", "RE_REVISE",
    "BATCH_DONE", "ERROR_COLLECT",
    "REPORT", "DONE",
]

# ---------- Phase config ----------

PHASE_CONFIG = {
    "BATCH_START": {"type": "noop"},
    "FETCH": {
        "type": "agent",
        "prompt": "skills/epic-decompose/prompts/fetch-agent.md",
        "ids_file": "tmp/pipeline-active-ids.txt",
        "poll_phase": "fetch",
        "vars": {"ID": "{ID}"},
    },
    "DECOMPOSE": {
        "type": "agent",
        "prompt": "skills/epic-decompose/prompts/decompose-agent.md",
        "ids_file": "tmp/pipeline-active-ids.txt",
        "poll_phase": "decompose",
        "vars": {"ID": "{ID}"},
    },
    "REVIEW_DECOMP": {
        "type": "agent",
        "prompt": "skills/epic-decompose/prompts/review-agent.md",
        "ids_file": "tmp/pipeline-active-ids.txt",
        "poll_phase": "review_decomp",
        "vars": {"ID": "{ID}"},
    },
    "REVISE_DECOMP": {
        "type": "agent",
        "prompt": "skills/epic-decompose/prompts/revise-agent.md",
        "ids_file": "tmp/pipeline-active-ids.txt",
        "poll_phase": "revise_decomp",
        "vars": {"ID": "{ID}"},
    },
    "RE_REVIEW_CHECK": {"type": "noop"},
    "RE_REVIEW": {
        "type": "agent",
        "prompt": "skills/epic-decompose/prompts/review-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",
        "poll_phase": "review_decomp",
        "vars": {"ID": "{ID}"},
    },
    "REVISE_CHECK": {"type": "noop"},
    "RE_REVISE": {
        "type": "agent",
        "prompt": "skills/epic-decompose/prompts/revise-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",
        "poll_phase": "revise_decomp",
        "vars": {"ID": "{ID}"},
    },

    # --- Batch control + retry ---
    "BATCH_DONE": {"type": "noop"},
    "ERROR_COLLECT": {
        "type": "script",
        "command": "python3 scripts/error_collect.py",
    },

    # --- Terminal ---
    "REPORT": {
        "type": "script",
        "command": ("python3 scripts/generate_run_report.py"
                    " --start-time {start_time}"
                    " --batch-size {batch_size}"),
    },
}

# ---------- State helpers ----------


def _load_state():
    if not os.path.exists(STATE_FILE):
        print(f"State file not found: {STATE_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(STATE_FILE) as f:
        state = yaml.safe_load(f)
    if not isinstance(state, dict):
        raise WorkloadError("Malformed pipeline state")
    bound_workload(state)
    return state


def _phase_config(state):
    return phase_config(state, PHASE_CONFIG.get(state["phase"], {"type": "noop"}))


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        yaml.dump(state, f, default_flow_style=False, sort_keys=False)


def _read_ids(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def _write_ids(path, ids):
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    with open(path, "w") as f:
        for id_ in ids:
            f.write(f"{id_}\n")


def _copy_ids(src, dst):
    os.makedirs(os.path.dirname(dst) or "tmp", exist_ok=True)
    shutil.copy2(src, dst)


def _validate_ids_individually(state, phase, ids_file, validator):
    """Keep one bad source from stopping the rest of a batch."""
    ids = _read_ids(ids_file)
    valid_ids = []
    for key in ids:
        status = read_status(key)
        if status:
            # Invalid sources are retried through ERROR_COLLECT. Intentional
            # skips are terminal and stay out of downstream phases.
            continue
        try:
            validator(state, phase, key)
        except WorkloadError as exc:
            write_status(key, "invalid", phase.lower(), str(exc))
            print(f"{phase}: {key} invalid: {exc}", file=sys.stderr)
            continue
        valid_ids.append(key)

    if valid_ids != ids:
        _write_ids(ids_file, valid_ids)
    return valid_ids


def _validate_completed_one(state, phase, key):
    validate_completed(state, phase, [key])


def _validate_inputs_one(state, phase, key):
    validate_inputs(state, phase, [key])


def _reset_revised_flag(decomp_path):
    """Remove ``revised`` key from decomposition frontmatter.

    The revise_decomp completion poller treats *any* non-None value
    (including False) as "completed".  Removing the key forces it back to
    "pending" so the revise agent is actually launched.
    """
    from artifact_utils import read_frontmatter
    data, body = read_frontmatter(decomp_path)
    if data and "revised" in data:
        del data["revised"]
        with open(decomp_path, "w") as f:
            f.write("---\n")
            f.write(yaml.dump(data, default_flow_style=False, sort_keys=False))
            f.write("---\n")
            f.write(body)


def _run_script(cmd):
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print("Script failed (exit code "
              f"{result.returncode})", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def _compute_ai_scores(ids_file, state):
    """Compute AI implementability scores from signal values in epic frontmatter."""
    ids = _read_ids(ids_file)
    if not ids:
        return
    from compute_ai_scores import compute_for_strategy
    validate_ids(state, ids)
    planning = bound_workload(state).planning
    for key in ids:
        compute_for_strategy(key, planning)


# ---------- Transition logic ----------

MAIN_SEQUENCE = ["FETCH", "DECOMPOSE", "REVIEW_DECOMP"]


def advance(state, dry_run=False):
    """Compute and apply the next phase transition.

    Returns (next_phase, summary_line).
    """
    phase = state["phase"]
    if not dry_run and phase in ("FETCH", "DECOMPOSE", "REVIEW_DECOMP", "REVISE_DECOMP", "RE_REVIEW", "RE_REVISE"):
        config = _phase_config(state)
        _validate_ids_individually(
            state, phase, config["ids_file"], _validate_completed_one)

    # --- BATCH_START: reset counters, populate active IDs ---
    if phase == "BATCH_START":
        batch = state.get("batch", 0) + 1
        if not dry_run:
            state["batch"] = batch
            batch_file = f"tmp/pipeline-batch-{batch}-ids.txt"
            if state.get("retry_cycle", 0) > 0:
                for key in _read_ids(batch_file):
                    status = read_status(key)
                    if status and status["status"] == "invalid":
                        clear_status(key)
            _copy_ids(batch_file, "tmp/pipeline-active-ids.txt")
        return "FETCH", f"BATCH_START → FETCH: batch={batch}"

    # --- Linear main sequence ---
    if phase in MAIN_SEQUENCE[:-1]:
        nxt = MAIN_SEQUENCE[MAIN_SEQUENCE.index(phase) + 1]
        if phase == "DECOMPOSE" and not dry_run:
            _compute_ai_scores("tmp/pipeline-active-ids.txt", state)
        return nxt, f"{phase} → {nxt}"

    # --- REVIEW_DECOMP → REVISE_DECOMP (unconditional first revision) ---
    if phase == "REVIEW_DECOMP":
        if not dry_run:
            # Clear the ``revised`` default so the poller sees IDs as pending.
            # The schema sets revised: false on creation, but the poller
            # treats any non-None value as "completed".
            for strat_id in _read_ids("tmp/pipeline-active-ids.txt"):
                decomp = f"artifacts/epic-tasks/{strat_id}-decomposition.md"
                if os.path.exists(decomp):
                    _reset_revised_flag(decomp)
        return "REVISE_DECOMP", "REVIEW_DECOMP → REVISE_DECOMP: first revision (unconditional)"

    # --- REVISE_DECOMP → RE_REVIEW_CHECK ---
    if phase == "REVISE_DECOMP":
        if not dry_run:
            _compute_ai_scores("tmp/pipeline-active-ids.txt", state)
        return "RE_REVIEW_CHECK", "REVISE_DECOMP → RE_REVIEW_CHECK"

    # --- RE_REVIEW_CHECK: did revision change anything? ---
    if phase == "RE_REVIEW_CHECK":
        from artifact_utils import read_frontmatter
        cycle = state.get("revise_cycle", 0)
        if cycle == 0:
            check_ids = _read_ids("tmp/pipeline-active-ids.txt")
        else:
            check_ids = _read_ids("tmp/pipeline-revise-ids.txt")
        revised_ids = []
        for strat_id in check_ids:
            decomp_path = f"artifacts/epic-tasks/{strat_id}-decomposition.md"
            if os.path.exists(decomp_path):
                try:
                    data, _ = read_frontmatter(decomp_path)
                    if data and data.get("revised"):
                        revised_ids.append(strat_id)
                except Exception:
                    pass
        if not revised_ids:
            return "BATCH_DONE", "RE_REVIEW_CHECK → BATCH_DONE: revision made no changes"
        if not dry_run:
            _write_ids("tmp/pipeline-revise-ids.txt", revised_ids)
            # Delete old review files so the poller can detect fresh reviews
            for strat_id in revised_ids:
                review_path = f"artifacts/epic-reviews/{strat_id}-decomp-review.md"
                if os.path.exists(review_path):
                    os.remove(review_path)
        return ("RE_REVIEW",
                f"RE_REVIEW_CHECK → RE_REVIEW: {len(revised_ids)} revised")

    # --- RE_REVIEW → REVISE_CHECK ---
    if phase == "RE_REVIEW":
        return "REVISE_CHECK", "RE_REVIEW → REVISE_CHECK"

    # --- REVISE_CHECK: only revise again if review fails, with cycle cap ---
    if phase == "REVISE_CHECK":
        from artifact_utils import read_frontmatter
        revise_ids = _read_ids("tmp/pipeline-revise-ids.txt")
        failing_ids = []
        for strat_id in revise_ids:
            review_path = f"artifacts/epic-reviews/{strat_id}-decomp-review.md"
            if os.path.exists(review_path):
                try:
                    data, _ = read_frontmatter(review_path)
                    if data and not data.get("pass", True):
                        failing_ids.append(strat_id)
                except Exception:
                    pass
        cycle = state.get("revise_cycle", 0)
        if failing_ids and cycle < 2:
            if not dry_run:
                state["revise_cycle"] = cycle + 1
                _write_ids("tmp/pipeline-revise-ids.txt", failing_ids)
                # Reset revised flag so poller detects new changes
                for strat_id in failing_ids:
                    decomp_path = f"artifacts/epic-tasks/{strat_id}-decomposition.md"
                    if os.path.exists(decomp_path):
                        _reset_revised_flag(decomp_path)
            return ("RE_REVISE",
                    f"REVISE_CHECK → RE_REVISE:"
                    f" failing={len(failing_ids)} cycle={cycle + 1}/2")
        return "BATCH_DONE", "REVISE_CHECK → BATCH_DONE: review passed or cycle cap reached"

    # --- RE_REVISE → RE_REVIEW_CHECK (loop back) ---
    if phase == "RE_REVISE":
        if not dry_run:
            _compute_ai_scores("tmp/pipeline-revise-ids.txt", state)
        return "RE_REVIEW_CHECK", "RE_REVISE → RE_REVIEW_CHECK"

    # --- BATCH_DONE decision ---
    if phase == "BATCH_DONE":
        batch = state.get("batch", 0)
        total = state.get("total_batches", 1)
        retry = state.get("retry_cycle", 0)
        active_ids = _read_ids("tmp/pipeline-active-ids.txt")
        batch_stats = f"{len(active_ids)} strategies"
        prefix = "Retry batch" if retry > 0 else "Batch"
        summary = f"{prefix} {batch}/{total} complete: {batch_stats}"
        if batch < total:
            return ("BATCH_START",
                    f"{summary}\nBATCH_DONE → BATCH_START")
        if retry < 1:
            # Check for errors
            all_ids = _read_ids("tmp/pipeline-all-ids.txt")
            error_ids = []
            statuses = all_statuses()
            for strat_id in all_ids:
                status = statuses.get(strat_id)
                if status and status["status"] == "skipped":
                    continue
                if status and status["status"] == "invalid":
                    error_ids.append(strat_id)
                    continue
                review_path = f"artifacts/epic-reviews/{strat_id}-decomp-review.md"
                if os.path.exists(review_path):
                    try:
                        from artifact_utils import read_frontmatter
                        data, _ = read_frontmatter(review_path)
                        if data and data.get("error"):
                            error_ids.append(strat_id)
                    except Exception:
                        pass
                decomp_path = f"artifacts/epic-tasks/{strat_id}-decomposition.md"
                if not os.path.exists(decomp_path):
                    error_ids.append(strat_id)
            error_ids = list(dict.fromkeys(error_ids))
            if error_ids:
                return ("ERROR_COLLECT",
                        f"{summary}\nBATCH_DONE → ERROR_COLLECT:"
                        f" errors={len(error_ids)}")
        return "REPORT", f"{summary}\nBATCH_DONE → REPORT"

    # --- ERROR_COLLECT → BATCH_START ---
    if phase == "ERROR_COLLECT":
        retry_ids = _read_ids("tmp/pipeline-retry-ids.txt")
        n = len(retry_ids)
        batch = state.get("total_batches", 0)
        return ("BATCH_START",
                f"ERROR_COLLECT: retry batch {batch} with {n} error IDs\n"
                f"ERROR_COLLECT → BATCH_START")

    # --- REPORT → DONE (with optional announce) ---
    if phase == "REPORT":
        if not dry_run and state.get("announce_complete"):
            _run_script("python3 scripts/finish.py")
        return "DONE", "REPORT → DONE"

    print(f"No transition defined for phase: {phase}", file=sys.stderr)
    sys.exit(1)


# ---------- CLI commands ----------


def cmd_init(args):
    parser = argparse.ArgumentParser(prog="pipeline_state.py init")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--announce-complete", action="store_true")
    parser.add_argument("--workload", help="Select a registered planning workload")
    opts = parser.parse_args(args)
    selected = get_workload(opts.workload or "rhaistrat-strategy")
    if selected.planning is None:
        raise WorkloadError(f"{selected.id}: no planning policy configured")

    os.makedirs("tmp", exist_ok=True)
    clear_all_statuses()
    for f in glob.glob("tmp/pipeline-batch-*-ids.txt"):
        os.remove(f)
    if os.path.exists(DISPATCH_MARKER):
        os.remove(DISPATCH_MARKER)
    state = {
        "phase": "INIT",
        "batch": 0,
        "total_batches": 0,
        "headless": opts.headless,
        "announce_complete": opts.announce_complete,
        "batch_size": opts.batch_size,
        "start_time": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "retry_cycle": 0,
        "workload_id": selected.id,
        "workload_version": selected.version,
        "workload_digest": selected.digest,
        "planning_digest": selected.planning.digest,
        "workload_explicit": opts.workload is not None,
        "source_bindings": {},
    }
    _save_state(state)
    print(f"Initialized pipeline state: batch_size={opts.batch_size}")


def cmd_get_phase(args):
    state = _load_state()
    print(state["phase"])


def cmd_set_phase(args):
    if not args or args[0] not in PHASES:
        print(f"Usage: set-phase <PHASE>\nValid phases: {', '.join(PHASES)}",
              file=sys.stderr)
        sys.exit(1)
    state = _load_state()
    state["phase"] = args[0]
    _save_state(state)
    print(args[0])


def cmd_get_phase_config(args):
    state = _load_state()
    phase = state["phase"]
    config = _phase_config(state)
    config["phase"] = phase
    config.pop("command", None)
    config.pop("pre_script", None)
    config.pop("post_verify", None)
    if config.get("type") == "script":
        config.pop("ids_file", None)
    if config.get("type") == "agent":
        max_concurrent = int(state.get("batch_size", 25))
        n_parallel = len(config.get("parallel", []))
        config["wave_size"] = max(1, max_concurrent // (1 + n_parallel))
    print(yaml.dump(config, default_flow_style=False, sort_keys=False),
          end="")


def cmd_run_phase(args):
    state = _load_state()
    phase = state["phase"]
    config = _phase_config(state)
    phase_type = config.get("type", "noop")
    if phase_type != "script":
        print(f"run-phase: phase {phase} is type '{phase_type}', not 'script'",
              file=sys.stderr)
        sys.exit(1)
    cmd = config["command"].format_map(state)
    if config.get("ids_file"):
        ids = _read_ids(config["ids_file"])
        if ids:
            cmd += " " + " ".join(ids)
        else:
            print(f"[run-phase] {phase}: no IDs, skipping")
            with open(DISPATCH_MARKER, "w") as f:
                f.write(phase)
            return
    print(f"[run-phase] {phase}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        sys.exit(result.returncode)
    with open(DISPATCH_MARKER, "w") as f:
        f.write(phase)


def cmd_set_wave(args):
    if not args:
        print("Usage: set-wave ID1 ID2 ...", file=sys.stderr)
        sys.exit(1)
    _write_ids(WAVE_IDS_FILE, args)
    print(f"Wave: {len(args)} IDs")


def cmd_next_action(args):
    from check_decompose_progress import check_id

    state = _load_state()
    phase = state["phase"]

    if phase == "DONE":
        print(yaml.dump({"action": "done", "message": "Pipeline complete"},
                        default_flow_style=False, sort_keys=False), end="")
        return

    if phase not in PHASES:
        print(f"next-action: phase '{phase}' is not dispatchable."
              " Run init and set-phase BATCH_START first.",
              file=sys.stderr)
        sys.exit(1)

    for _ in range(MAX_NEXT_ACTION_ITERATIONS):
        phase = state["phase"]
        config = _phase_config(state)
        phase_type = config.get("type", "noop")

        # --- DONE ---
        if phase == "DONE":
            print(yaml.dump(
                {"action": "done", "message": "Pipeline complete"},
                default_flow_style=False, sort_keys=False), end="")
            return

        # --- Noop: advance and loop ---
        if phase_type == "noop":
            next_phase, summary = advance(state)
            state["phase"] = next_phase
            _save_state(state)
            print(summary, file=sys.stderr)
            continue

        # --- Script: check dispatch marker ---
        if phase_type == "script":
            if os.path.exists(DISPATCH_MARKER):
                with open(DISPATCH_MARKER) as f:
                    marker_phase = f.read().strip()
                if marker_phase == phase:
                    os.remove(DISPATCH_MARKER)
                    next_phase, summary = advance(state)
                    state["phase"] = next_phase
                    _save_state(state)
                    print(summary, file=sys.stderr)
                    continue
                else:
                    os.remove(DISPATCH_MARKER)
            print(yaml.dump(
                {"action": "run_script", "phase": phase,
                 "message": f"{phase}: run-phase"},
                default_flow_style=False, sort_keys=False), end="")
            return

        # --- Agent: compute next wave ---
        if phase_type == "agent":
            ids_file = config.get("ids_file", "")
            all_ids = _validate_ids_individually(
                state, phase, ids_file, _validate_inputs_one)
            poll_phase = config.get("poll_phase", "")

            phases_to_check = [poll_phase] if poll_phase else []
            for p in config.get("parallel", []):
                if p.get("poll_phase"):
                    phases_to_check.append(p["poll_phase"])

            remaining = []
            for strat_id in all_ids:
                for pphase in phases_to_check:
                    if check_id(pphase, strat_id) == "pending":
                        remaining.append(strat_id)
                        break

            if not remaining:
                if config.get("post_verify"):
                    _run_script(config["post_verify"])
                next_phase, summary = advance(state)
                state["phase"] = next_phase
                _save_state(state)
                print(summary, file=sys.stderr)
                continue

            max_concurrent = int(state.get("batch_size", 25))
            n_parallel = len(config.get("parallel", []))
            wave_size = max(1, max_concurrent // (1 + n_parallel))

            wave_ids = remaining[:wave_size]
            wave_num = 1 + (len(all_ids) - len(remaining)) // wave_size
            total_waves = max(1, -(-len(all_ids) // wave_size))

            if config.get("pre_script"):
                for strat_id in wave_ids:
                    cmd = config["pre_script"].replace("{ID}", strat_id)
                    _run_script(cmd)

            _write_ids(WAVE_IDS_FILE, wave_ids)

            agents = []
            for strat_id in wave_ids:
                entry = {}
                if config.get("subagent_type"):
                    entry["subagent_type"] = config["subagent_type"]
                entry["prompt_file"] = config["prompt"]
                var_lines = []
                for k, v in config.get("vars", {}).items():
                    var_lines.append(
                        f"{k}={v.replace('{ID}', strat_id)}")
                entry["vars"] = "\n".join(var_lines) + "\n"
                agents.append(entry)

                for par in config.get("parallel", []):
                    pentry = {}
                    if par.get("subagent_type"):
                        pentry["subagent_type"] = par["subagent_type"]
                    pentry["prompt_file"] = par["prompt"]
                    pvar_lines = []
                    for k, v in par.get("vars", {}).items():
                        pvar_lines.append(
                            f"{k}={v.replace('{ID}', strat_id)}")
                    pentry["vars"] = "\n".join(pvar_lines) + "\n"
                    agents.append(pentry)

            msg = (f"{phase}: wave {wave_num}/{total_waves}"
                   f" ({len(wave_ids)} IDs)")
            output = {
                "action": "launch_wave",
                "phase": phase,
                "message": msg,
                "agents": agents,
            }
            print(yaml.dump(output, Dumper=_BlockDumper,
                            default_flow_style=False, sort_keys=False),
                  end="")
            return

    print(f"next-action: exceeded {MAX_NEXT_ACTION_ITERATIONS} iterations"
          f" at phase {state['phase']}", file=sys.stderr)
    sys.exit(1)


def cmd_wait_for_wave(args):
    if not os.path.exists(WAVE_IDS_FILE):
        print("wait-for-wave: no wave file found"
              f" ({WAVE_IDS_FILE}). Run next-action first.",
              file=sys.stderr)
        sys.exit(1)

    wave_ids = _read_ids(WAVE_IDS_FILE)
    if not wave_ids:
        print("wait-for-wave: wave file is empty."
              " All agents may already be complete.",
              file=sys.stderr)
        return

    state = _load_state()
    phase = state["phase"]
    config = PHASE_CONFIG.get(phase, {"type": "noop"})

    poll_phase = config.get("poll_phase")
    if not poll_phase:
        print(f"wait-for-wave: phase {phase} has no poll_phase",
              file=sys.stderr)
        sys.exit(1)

    cmd_parts = [
        sys.executable,
        os.path.join(os.path.dirname(__file__),
                     "check_decompose_progress.py"),
        "--wait",
        "--max-wait", "90",
        "--phase", poll_phase,
    ]
    for p in config.get("parallel", []):
        if p.get("poll_phase"):
            cmd_parts.extend(["--also-phase", p["poll_phase"]])
    if not state.get("headless", True):
        cmd_parts.append("--fast-poll")
    cmd_parts.extend(["--id-file", WAVE_IDS_FILE])

    result = subprocess.run(cmd_parts)
    if result.returncode == 0:
        return
    if result.returncode == 3:
        print("Re-run: python3 scripts/pipeline_state.py wait-for-wave")
        sys.exit(3)
    print(f"wait-for-wave: check_decompose_progress.py exited with"
          f" code {result.returncode}", file=sys.stderr)
    sys.exit(result.returncode)


def _check_agent_phase_complete(config):
    ids_file = config.get("ids_file")
    poll_phase = config.get("poll_phase")
    if not ids_file or not poll_phase:
        return True
    ids = _read_ids(ids_file)
    if not ids:
        return True
    from check_decompose_progress import check_id
    phases_to_check = [poll_phase]
    for p in config.get("parallel", []):
        if p.get("poll_phase"):
            phases_to_check.append(p["poll_phase"])
    for phase in phases_to_check:
        for strat_id in ids:
            if check_id(phase, strat_id) == "pending":
                return False
    return True


def cmd_advance(args):
    dry_run = "--dry-run" in args
    state = _load_state()
    phase = state["phase"]
    config = PHASE_CONFIG.get(phase, {"type": "noop"})
    phase_type = config.get("type", "noop")
    if phase_type == "script" and not dry_run:
        if not os.path.exists(DISPATCH_MARKER):
            print(f"advance: script phase {phase} was not dispatched."
                  " Run: python3 scripts/pipeline_state.py next-action",
                  file=sys.stderr)
            sys.exit(1)
        with open(DISPATCH_MARKER) as f:
            marker_phase = f.read().strip()
        os.remove(DISPATCH_MARKER)
        if marker_phase != phase:
            print(f"advance: dispatch marker is for {marker_phase},"
                  f" not current phase {phase}", file=sys.stderr)
            sys.exit(1)
    if phase_type == "agent" and not dry_run:
        if not _check_agent_phase_complete(config):
            print(f"advance: agent phase {phase} has pending agents."
                  f" Run: python3 scripts/pipeline_state.py"
                  f" wait-for-wave",
                  file=sys.stderr)
            sys.exit(1)
    next_phase, summary = advance(state, dry_run=dry_run)
    if not dry_run:
        state["phase"] = next_phase
        _save_state(state)
    print(summary)


def cmd_set(args):
    if not args:
        print("Usage: set key=value ...", file=sys.stderr)
        sys.exit(1)
    state = _load_state()
    for arg in args:
        if "=" not in arg:
            print(f"Invalid key=value: {arg}", file=sys.stderr)
            sys.exit(1)
        k, v = arg.split("=", 1)
        if k in BOUND_FIELDS:
            raise WorkloadError(f"Cannot change bound pipeline field {k}")
        if v.isdigit():
            v = int(v)
        elif v.lower() in ("true", "false"):
            v = v.lower() == "true"
        state[k] = v
    _save_state(state)


def cmd_get(args):
    if not args:
        print("Usage: get <key>", file=sys.stderr)
        sys.exit(1)
    state = _load_state()
    val = state.get(args[0])
    if val is None:
        sys.exit(1)
    print(val)


def cmd_status(args):
    state = _load_state()
    print(yaml.dump(state, default_flow_style=False, sort_keys=False),
          end="")


def cmd_diagnose(args):
    state = _load_state()
    phase = state["phase"]
    print(f"Phase: {phase}")
    print(f"Batch: {state.get('batch', 0)}/{state.get('total_batches', 0)}")
    print(f"Revise cycle: {state.get('revise_cycle', 0)}/2")
    print(f"Retry cycle: {state.get('retry_cycle', 0)}/1")

    id_files = [
        "tmp/pipeline-all-ids.txt",
        "tmp/pipeline-active-ids.txt",
        "tmp/pipeline-revise-ids.txt",
        "tmp/pipeline-retry-ids.txt",
    ]
    print("\nID files:")
    for f in id_files:
        if os.path.exists(f):
            ids = _read_ids(f)
            print(f"  {f}: {len(ids)} IDs")
        else:
            print(f"  {f}: (missing)")

    active = _read_ids("tmp/pipeline-active-ids.txt")
    if active:
        missing_strat = []
        missing_decomp = []
        error_ids = []
        for strat_id in active:
            if not os.path.exists(f"artifacts/strat-tasks/{strat_id}.md"):
                missing_strat.append(strat_id)
            decomp = f"artifacts/epic-tasks/{strat_id}-decomposition.md"
            if not os.path.exists(decomp):
                missing_decomp.append(strat_id)
            review = f"artifacts/epic-reviews/{strat_id}-decomp-review.md"
            if os.path.exists(review):
                try:
                    from artifact_utils import read_frontmatter
                    data, _ = read_frontmatter(review)
                    if data and data.get("error"):
                        error_ids.append(strat_id)
                except Exception:
                    pass
        print(f"\nActive IDs: {len(active)}")
        if missing_strat:
            print(f"  Missing strategy files: {', '.join(missing_strat)}")
        if missing_decomp:
            print(f"  Missing decomposition files: {', '.join(missing_decomp)}")
        if error_ids:
            print(f"  Error IDs: {', '.join(error_ids)}")


DISPATCH_LOOP = """\
Resume the dispatch loop:
  1. python3 scripts/pipeline_state.py next-action
  2. If action == done: exit loop, run teardown
  3. If action == run_script: python3 scripts/pipeline_state.py run-phase, then go to 1
  4. If action == launch_wave:
     a. For each agent in agents: launch background Agent(prompt=vars + "\\n\\nRead " + prompt_file + " and follow all instructions exactly.", subagent_type if present)
     b. python3 scripts/pipeline_state.py wait-for-wave (re-run on exit 3), then go to 1"""


def cmd_dispatch_context(args):
    if not os.path.exists(STATE_FILE):
        return
    state = _load_state()
    phase = state["phase"]
    if phase not in PHASES:
        print(f"[PIPELINE STATE RECOVERY] Setup in progress (phase: {phase})")
        print("Setup is not yet complete. Re-read SKILL.md"
              " (skills/epic-decompose/SKILL.md) and resume"
              " the setup steps from where you left off.")
        return
    if phase == "DONE":
        print("[PIPELINE STATE RECOVERY] Pipeline complete (phase: DONE)")
        return
    config = PHASE_CONFIG.get(phase, {"type": "noop"})
    phase_type = config.get("type", "noop")
    print(f"[PIPELINE STATE RECOVERY] Current phase: {phase}"
          f" (type: {phase_type})")
    print(f"Batch: {state.get('batch', 0)}/{state.get('total_batches', 0)}")
    print()
    print(DISPATCH_LOOP)


def cmd_post_compact_hook(args):
    if not os.environ.get("EPIC_CREATOR_ENABLE_CONTEXT_HOOK"):
        return
    cmd_dispatch_context(args)


COMMANDS = {
    "init": cmd_init,
    "get-phase": cmd_get_phase,
    "set-phase": cmd_set_phase,
    "get-phase-config": cmd_get_phase_config,
    "run-phase": cmd_run_phase,
    "set-wave": cmd_set_wave,
    "next-action": cmd_next_action,
    "wait-for-wave": cmd_wait_for_wave,
    "advance": cmd_advance,
    "set": cmd_set,
    "get": cmd_get,
    "status": cmd_status,
    "diagnose": cmd_diagnose,
    "dispatch-context": cmd_dispatch_context,
    "post-compact-hook": cmd_post_compact_hook,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Commands: {', '.join(COMMANDS)}", file=sys.stderr)
        sys.exit(1)
    try:
        COMMANDS[sys.argv[1]](sys.argv[2:])
    except WorkloadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
