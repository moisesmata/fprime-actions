#!/usr/bin/env bash
#
# Periodic soak test

set -uo pipefail

# Namespace by deployment name
INSTALL_DIR="${HOME}/fprime-soak-${DEPLOYMENT_NAME}"
DICT=$(ls "${INSTALL_DIR}"/dict/*TopologyDictionary.json)

# Analyze accumulated telemetry. Prefers GDS text logs; ComLogger is fallback.
echo "[INFO] Analyzing soak telemetry"
"${INSTALL_DIR}/venv/bin/python" "${ACTION_PATH}/scripts/soak_monitor.py" \
  --dictionary "${DICT}" \
  --gds-logs "${INSTALL_DIR}/gds-logs" \
  --com-logs "${INSTALL_DIR}/ComLoggerFiles" \
  --soak-log "${INSTALL_DIR}/soak.log"
monitor_rc=$?

# Run the deployment's integration tests.
echo "[INFO] Running integration tests"
cd "${INSTALL_DIR}/test"
. "${INSTALL_DIR}/venv/bin/activate"

# Use namespaced ZMQ sockets so tests connect to the correct GDS
ZMQ_IN="ipc:///tmp/fprime-server-${DEPLOYMENT_NAME}-in"
ZMQ_OUT="ipc:///tmp/fprime-server-${DEPLOYMENT_NAME}-out"

# Override pytest's default python_files=test_*.py so deployments that name
# tests <something>_integration_tests.py (e.g. led-blinker) still get collected.
pytest -o python_files='*.py' \
  --dictionary "${DICT}" \
  --zmq-transport "${ZMQ_IN}" "${ZMQ_OUT}"
pytest_rc=$?

# Fail the job on either the monitor or pytest, but run both first.
if [ "${monitor_rc}" -ne 0 ]; then
  exit "${monitor_rc}"
fi
if [ "${pytest_rc}" -ne 0 ]; then
  exit "${pytest_rc}"
fi

echo "[INFO] Soak test passed"
