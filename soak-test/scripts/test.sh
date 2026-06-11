#!/usr/bin/env bash
#
# Periodic soak test: confirm services are healthy, analyze accumulated logs,
# then run integration tests. On any failure (FSW dead, FATAL event, pytest
# fail) we disable the calling workflow so cron stops cluttering the Actions
# page until a human investigates and re-enables it.
set -uo pipefail

INSTALL_DIR="${HOME}/fprime-soak"
DICT=$(ls "${INSTALL_DIR}"/dict/*TopologyDictionary.json | head -n1)

# disable_workflow: best-effort halt of the cron. Requires actions:write on
# GITHUB_TOKEN (set in the workflow's permissions block). If the repo's token
# policy doesn't permit it, we warn rather than masking the original failure.
disable_workflow() {
  local reason="$1"
  echo "::error::${reason}"
  if [ -z "${GH_TOKEN:-}" ] || [ -z "${GITHUB_REPOSITORY:-}" ] || [ -z "${GITHUB_WORKFLOW_REF:-}" ]; then
    echo "::warning::GH_TOKEN/GITHUB_REPOSITORY/GITHUB_WORKFLOW_REF unset; cannot disable workflow"
    return
  fi
  # GITHUB_WORKFLOW_REF is e.g. 'owner/repo/.github/workflows/foo.yml@refs/heads/main'.
  # gh wants just the workflow filename.
  local wf
  wf="$(basename "${GITHUB_WORKFLOW_REF%@*}")"
  echo "[INFO] Disabling workflow '${wf}' on ${GITHUB_REPOSITORY}"
  if ! gh workflow disable "${wf}" --repo "${GITHUB_REPOSITORY}"; then
    echo "::warning::Failed to disable workflow (token may lack actions:write); manual disable required"
  fi
}

# 1. FSW must be alive. With Restart=no, a crashed FSW stays down, and any
#    further soak run is meaningless until someone investigates.
if ! sudo systemctl is-active --quiet fprime-soak-fsw; then
  sudo systemctl status fprime-soak-fsw --no-pager -l || true
  sudo journalctl -u fprime-soak-fsw --no-pager -n 40 || true
  disable_workflow "fprime-soak-fsw is not active"
  exit 1
fi

# 2. Analyze accumulated telemetry. soak_monitor exits non-zero on FATAL.
echo "[INFO] Analyzing soak telemetry"
"${INSTALL_DIR}/venv/bin/python" "${ACTION_PATH}/scripts/soak_monitor.py" \
  --dictionary "${DICT}" \
  --logs "$(mktemp -d)" \
  --com-logs "${INSTALL_DIR}/ComLoggerFiles"
monitor_rc=$?

# 3. Run the deployment's integration tests.
echo "[INFO] Running integration tests"
cd "${INSTALL_DIR}/test"
. "${INSTALL_DIR}/venv/bin/activate"
# Override pytest's default python_files=test_*.py so deployments that name
# tests <something>_integration_tests.py (e.g. led-blinker) still get collected.
# --dictionary is required by fprime-gds's StandardPipelineParser, which the
# fprime_test_api fixture invokes when no settings.ini lives above CWD.
pytest -o python_files='*.py' --dictionary "${DICT}"
pytest_rc=$?

# 4. Disable the cron on any failure so subsequent scheduled runs don't pile
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
