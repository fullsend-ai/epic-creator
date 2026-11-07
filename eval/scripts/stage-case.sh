#!/usr/bin/env bash
# before_each staging hook for the epic-decompose eval.
#
# The harness symlinks scripts/, skills/, CLAUDE.md and .claude/* from the project
# root into every case workspace. Those symlinks resolve to the REAL repo, so
# anything the pipeline writes through them lands in the working tree — which is
# exactly how a previous run left epic artifacts and a synthetic component list
# behind in the repo. Replace them with real copies so the workspace is
# self-contained and writes stay throwaway.
#
# NOT touched (case-provided, must win over anything from the repo):
#   .context/                  curated per-case architecture context + component list
#   artifacts/strat-tasks/     the pre-provisioned strategy under test
# The harness only symlinks a name when the case dir did not already provide it
# (`target.exists() and not link.exists()`), so these are already real copies.
#
# Harness-injected env: AGENT_EVAL_PROJECT_ROOT, CASE_WORKSPACE, CASE_ID.
set -euo pipefail

WS="${CASE_WORKSPACE:?CASE_WORKSPACE not set}"
ROOT="${AGENT_EVAL_PROJECT_ROOT:?AGENT_EVAL_PROJECT_ROOT not set}"

log() { echo "[stage-case] ${CASE_ID:-?}: $*" >&2; }
cd "$WS" || { log "FATAL: cannot cd $WS"; exit 1; }

# --- 1. De-symlink the project tree (~372KB per case) ------------------------
for name in scripts skills workloads CLAUDE.md; do
  if [ -L "$name" ]; then
    rm -f "$name"
    if [ -e "$ROOT/$name" ]; then
      cp -R "$ROOT/$name" "$name"
      log "copied $name"
    fi
  fi
  if [ "$name" = workloads ] && [ ! -e "$name" ] && [ -d "$ROOT/$name" ]; then
    cp -R "$ROOT/$name" "$name"
    log "copied workload registry and policies"
  fi
done

# .claude/* subdirectories are symlinked individually (settings.json is generated
# by the harness and must be left alone).
if [ -d .claude ]; then
  for sub in .claude/*; do
    [ -L "$sub" ] || continue
    target="$(readlink "$sub")"
    rm -f "$sub"
    [ -e "$target" ] && cp -R "$target" "$sub" && log "copied $sub"
  done
fi

# --- 2. Drop the repo from additionalDirectories -----------------------------
# The harness grants the agent read/write access to the project root so it can
# follow those symlinks. With real copies in place that grant is unnecessary, and
# leaving it lets Read/Write/Edit reach the working tree.
# (Bash is not path-scoped, so this narrows the surface rather than sealing it —
# a `cd $REPO && ...` still works. OS sandboxing is the hard boundary.)
SETTINGS=".claude/settings.json"
if [ -f "$SETTINGS" ]; then
  python3 - "$SETTINGS" "$ROOT" <<'PY'
import json, sys
path, root = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)
perms = cfg.get("permissions") or {}
dirs = perms.get("additionalDirectories") or []
kept = [d for d in dirs if d.rstrip("/") != root.rstrip("/")]
if len(kept) != len(dirs):
    perms["additionalDirectories"] = kept
    cfg["permissions"] = perms
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"[stage-case] removed project root from additionalDirectories", file=sys.stderr)
PY
fi

# --- 3. Assert the case-provided inputs survived ------------------------------
missing=0
for required in .context/rhai-components.txt artifacts/strat-tasks input.yaml; do
  [ -e "$required" ] || { log "MISSING $required"; missing=1; }
done
[ -L .context ] && { log "FATAL: .context is a symlink to the repo, not a case copy"; exit 1; }
[ "$missing" -eq 0 ] || { log "FATAL: case workspace incomplete"; exit 1; }

# --- 4. Re-baseline the git snapshot -----------------------------------------
# workspace.py commits the initial state BEFORE this hook runs, and collect.py
# diffs against HEAD to find in-place edits. Without a fresh commit, every file
# this hook copied (scripts/, skills/, CLAUDE.md, .claude/*) reads as "modified",
# floods outputs[modified_files], and blew two LLM-judge prompts past the 1M
# token limit on the 2026-08-19 run.
if [ -d .git ]; then
  git add -A >/dev/null 2>&1 || true
  GIT_AUTHOR_NAME=eval-harness GIT_AUTHOR_EMAIL=eval@harness \
  GIT_COMMITTER_NAME=eval-harness GIT_COMMITTER_EMAIL=eval@harness \
    git commit -q -m "staged" --allow-empty >/dev/null 2>&1 || true
  log "git baseline re-committed after staging"
  # The runner stages the plugin into .staged-plugins/ AFTER this hook runs;
  # exclude it so collect.py's modified-files diff never picks up the copy.
  printf '.staged-plugins/\n' >> .git/info/exclude 2>/dev/null || true
fi

log "workspace self-contained ($(find .context -name '*.md' | wc -l | tr -d ' ') arch docs, $(ls artifacts/strat-tasks | wc -l | tr -d ' ') strategy file)"
