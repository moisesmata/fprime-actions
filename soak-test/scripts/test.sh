#!/usr/bin/env bash
#
# Periodic soak test. On any failure (FATAL event, pytest
# fail) we disable the calling workflow so cron stops cluttering the Actions
# page until a human investigates and re-enables it.
set -uo pipefail

INSTALL_DIR="${HOME}/fprime-soak"
DICT=$(ls "${INSTALL_DIR}"/dict/*TopologyDictionary.json | head -n1)

# disable_workflow: best-effort halt of the cron. 
disable_workflow() {
  local reason="$1"
  echo "::error::${reason}"
  local wf
  wf="$(basename "${GITHUB_WORKFLOW_REF%@*}")"
  echo "[INFO] Disabling workflow '${wf}' on ${GITHUB_REPOSITORY}"
  if ! gh workflow disable "${wf}" --repo "${GITHUB_REPOSITORY}"; then
    echo "::warning::Failed to disable workflow, manual disable required"
  fi
}

# 1. Analyze accumulated telemetry. soak_monitor exits non-zero on FATAL.
echo "[INFO] Analyzing soak telemetry"
"${INSTALL_DIR}/venv/bin/python" "${ACTION_PATH}/scripts/soak_monitor.py" \
  --dictionary "${DICT}" \
  --logs "$(mktemp -d)" \
  --com-logs "${INSTALL_DIR}/ComLoggerFiles"
monitor_rc=$?

# 2. Run the deployment's integration tests.
echo "[INFO] Running integration tests"
cd "${INSTALL_DIR}/test"
. "${INSTALL_DIR}/venv/bin/activate"
# Override pytest's default python_files=test_*.py so deployments that name
# tests <something>_integration_tests.py (e.g. led-blinker) still get collected.
pytest -o python_files='*.py' --dictionary "${DICT}"
pytest_rc=$?

# 3. Disable the cron on any failure so subsequent scheduled runs don't pile
#    up red badges against an already-broken deployment.
if [ "${monitor_rc}" -ne 0 ]; then
  disable_workflow "Soak monitor detected a FATAL condition (exit ${monitor_rc})"
  exit "${monitor_rc}"
fi
if [ "${pytest_rc}" -ne 0 ]; then
  disable_workflow "Integration tests failed (pytest exit ${pytest_rc})"
  exit "${pytest_rc}"
fi

echo "[INFO] Soak test passed"
