#!/usr/bin/env bash
# post-epic-creator.sh — Report agent-result.json after the sandbox exits.
set -euo pipefail

: "${TARGET_REPO_DIR:?TARGET_REPO_DIR is required}"
: "${FULLSEND_VALIDATED_ITERATION_DIR:?FULLSEND_VALIDATED_ITERATION_DIR is required}"
: "${REPO_DIR:?REPO_DIR is required}"

RESULT_FILE="${FULLSEND_VALIDATED_ITERATION_DIR}/agent-result.json"

if [[ ! -f "${RESULT_FILE}" ]]; then
  echo "ERROR: agent-result.json not found at ${RESULT_FILE}, $(pwd)"
  exit 1
fi

ACTION=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["action"])' "${RESULT_FILE}")
PIPELINE=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("pipeline", ""))' "${RESULT_FILE}")
SUMMARY=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("summary", ""))' "${RESULT_FILE}")

echo "Result: action=${ACTION} pipeline=${PIPELINE}"
echo "Summary: ${SUMMARY}"

if [[ -d "${REPO_DIR}/tmp" ]]; then
  rm -rf "${TARGET_REPO_DIR}/tmp"
  cp -R -p "${REPO_DIR}/tmp" "${TARGET_REPO_DIR}/"
fi

if [[ -d "${REPO_DIR}/artifacts" ]]; then
  rm -rf "${TARGET_REPO_DIR}/artifacts"
  cp -R -p "${REPO_DIR}/artifacts" "${TARGET_REPO_DIR}/"
fi

case "${ACTION}" in
  completed|skipped) exit 0 ;;
  failed) echo "agent reported failure" >&2; exit 1 ;;
  *) echo "unknown action: ${ACTION}" >&2; exit 1 ;;
esac
