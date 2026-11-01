#!/usr/bin/env bash
# Host-side bootstrap, run before the sandbox exists: fetch architecture
# context and Jira project components into the checkout.
set -euo pipefail

: "${TARGET_REPO_DIR:?TARGET_REPO_DIR must be set}"
echo "epic-creator pre-script: bootstrapping ${TARGET_REPO_DIR}"

cd -- "${TARGET_REPO_DIR}"
bash scripts/fetch-architecture-context.sh
python3 scripts/fetch_components.py
echo "epic-creator pre-script: complete"
