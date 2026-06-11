#!/usr/bin/env bash
#
# Periodic soak test: analyze accumulated logs, then run integration tests

set -euo pipefail

INSTALL_DIR="${HOME}/fprime-soak"
DICT=$(ls "${INSTALL_DIR}"/dict/*TopologyDictionary.json | head -n1)

sudo systemctl is-active --quiet fprime-soak-fsw \
  || { echo "::error::fprime-soak-fsw is not active"; exit 1; }

echo "[INFO] Analyzing soak telemetry"
"${INSTALL_DIR}/venv/bin/python" "${ACTION_PATH}/scripts/soak_monitor.py" \
  --dictionary "${DICT}" \
  --logs "$(mktemp -d)" \
  --com-logs "${INSTALL_DIR}/ComLoggerFiles"

echo "[INFO] Running integration tests"
cd "${INSTALL_DIR}/test"
. "${INSTALL_DIR}/venv/bin/activate"
# Override pytest's default python_files=test_*.py so deployments that name
# tests <something>_integration_tests.py (e.g. led-blinker) still get collected.
pytest -o python_files='*.py'
