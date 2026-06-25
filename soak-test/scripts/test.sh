#!/usr/bin/env bash
#
# Periodic soak test

set -uo pipefail

INSTALL_DIR="${HOME}/fprime-soak"
DICT=$(ls "${INSTALL_DIR}"/dict/*TopologyDictionary.json)

# Analyze accumulated telemetry. Checks GDS text logs first
echo "[INFO] Analyzing soak telemetry"
"${INSTALL_DIR}/venv/bin/python" "${ACTION_PATH}/scripts/soak_monitor.py" \
  --dictionary "${DICT}" \
  --gds-logs "${INSTALL_DIR}/gds-logs" \
  --com-logs "${INSTALL_DIR}/ComLoggerFiles" \
  --soak-database "${INSTALL_DIR}/soak-database.log"
monitor_rc=$?

# Run the deployment's integration tests.
echo "[INFO] Running integration tests"
cd "${INSTALL_DIR}/test"
. "${INSTALL_DIR}/venv/bin/activate"
# Override pytest's default python_files=test_*.py so deployments that name
# tests <something>_integration_tests.py (e.g. led-blinker) still get collected.
pytest -o python_files='*.py' --dictionary "${DICT}"
pytest_rc=$?

# Exit with proper return value
if [ "${monitor_rc}" -ne 0 ]; then
  exit "${monitor_rc}"
fi
if [ "${pytest_rc}" -ne 0 ]; then
  exit "${pytest_rc}"
fi

echo "[INFO] Soak test passed"
