#!/usr/bin/env bash
#
# Periodic soak test: analyze accumulated telemetry, fire a short stress
# spike, then run the deployment's integration tests.

set -uo pipefail

# Namespace by deployment name
INSTALL_DIR="${HOME}/fprime-soak-${DEPLOYMENT_NAME}"
DICT=$(ls "${INSTALL_DIR}"/dict/*TopologyDictionary.json)

# Cron-phase lock: the deployment's flight-ops rotation loop pauses while
# this file exists so cron-phase load is the only load. Namespaced per
# deployment; never shared. The trap guarantees the rotation resumes even if
# a phase fails; the short sleep lets the loop's current action finish.
CRON_LOCK="${INSTALL_DIR}/.cron-active"
touch "${CRON_LOCK}"
trap 'rm -f "${CRON_LOCK}"' EXIT
sleep 5

# Analyze accumulated telemetry. Prefers GDS text logs; ComLogger is fallback.
echo "[INFO] Analyzing soak telemetry"
"${INSTALL_DIR}/venv/bin/python" "${ACTION_PATH}/scripts/soak_monitor.py" \
  --dictionary "${DICT}" \
  --gds-logs "${INSTALL_DIR}/gds-logs" \
  --com-logs "${INSTALL_DIR}/ComLoggerFiles" \
  --soak-log "${INSTALL_DIR}/soak.log"
monitor_rc=$?

# Short, sharp stress spike: the flight-ops rotation script in one-shot spike
# mode (same command-sending path as the between-cron loop). Command burst to
# saturate CmdDispatcher, file downlink burst (Linux only), and parameter-save
# churn. Runs while the cron lock holds the nominal loop off the bus; the
# integration tests below then double as the post-spike health check.
echo "[INFO] Running stress spike"
"${INSTALL_DIR}/flight-ops-rotation.sh" spike
spike_rc=$?

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

# Fail the job on the monitor, the spike, or the test suite - but run all of
# them first.
if [ "${monitor_rc}" -ne 0 ]; then
  exit "${monitor_rc}"
fi
if [ "${spike_rc}" -ne 0 ]; then
  exit "${spike_rc}"
fi
if [ "${pytest_rc}" -ne 0 ]; then
  exit "${pytest_rc}"
fi

echo "[INFO] Soak test passed"
