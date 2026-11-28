#!/usr/bin/env bash
# Host-side bootstrap, run before the sandbox exists: fetch architecture
# context and Jira project components into the checkout.
set -euo pipefail

: "${TARGET_REPO_DIR:?TARGET_REPO_DIR must be set}"
echo "epic-creator pre-script: bootstrapping ${TARGET_REPO_DIR}"

cd -- "${TARGET_REPO_DIR}"
bash scripts/fetch-architecture-context.sh
python3 scripts/fetch_components.py
# Repository resolution (ADR-0176): the sandbox has no GitHub access, so the
# declared sources are loaded here on the host. A malformed flag or a source
# that cannot be loaded stops the run before the sandbox starts.
if [ -n "${EPIC_CREATOR_REPOSITORY_MODE:-}" ]; then
  repository_mode="$(python3 scripts/repository_resolution.py flag)"
  if [ "${repository_mode}" = "on" ]; then
    python3 scripts/repository_resolution.py load
  fi
fi
echo "epic-creator pre-script: complete"
