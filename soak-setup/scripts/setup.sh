#!/usr/bin/env bash
#
# Soak setup: copy the build artifacts into $HOME/fprime-soak-<deployment> and build the
# soak virtualenv.
#
# Expected artifact layout (staged by the calling workflow into ./artifacts/):
#   artifacts/build-artifacts/<arch>/<deployment>/bin/<binary>
#   artifacts/build-artifacts/<arch>/<deployment>/dict/*TopologyDictionary.json
#   artifacts/int/
#   artifacts/fprime-gds.yml

set -euo pipefail

# Validate required environment variables
if [ -z "${DEPLOYMENT_NAME:-}" ]; then
  echo "::error::DEPLOYMENT_NAME is required"
  exit 1
fi

# Namespace by deployment name to allow multiple deployments on one runner
INSTALL_DIR="${HOME}/fprime-soak-${DEPLOYMENT_NAME}"
TEMPLATES="${ACTION_PATH}/templates"
SERVICE_NAME="fprime-soak-gds-${DEPLOYMENT_NAME}"

render() {
  sed -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
      -e "s#__SERVICE_USER__#$(whoami)#g" \
      -e "s#__GDS_ARGS__#${GDS_ARGS}#g" \
      -e "s#__DICT_PATH__#${DICT_PATH}#g" "$1"
}

# Delete anything that was there
rm -rf "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"/{bin,dict,gds-logs,ComLoggerFiles,test}

# Soak database: append-only record of trend telemetry, FSW events, and
# monitor-derived alerts across the full soak lifetime. Lets us delete the events.log and channels.log
echo "# SOAK STARTED $(date +%Y-%m-%dT%H:%M:%S)" > "${INSTALL_DIR}/soak-database.log"

# Platform-specific artifact handling
PLATFORM="${PLATFORM:-linux}"

if [[ "${PLATFORM}" == "pico2-fprime-ci" ]]; then
  # Pico 2 via fprime-ci flow - archive.tar.gz was already extracted by fprime-ci
  # build outputs are in ./build-artifacts/
  echo "[INFO] Setting up Pico 2 artifacts from fprime-ci build"

  # fprime-ci extracted archive.tar.gz into ./build-artifacts/
  # Find the dictionary (location defined in pico2.yml)
  DICT_FILE=$(find ./build-artifacts -name "*TopologyDictionary.json" | head -n 1)
  if [ -z "${DICT_FILE}" ]; then
    echo "::error::Could not find TopologyDictionary.json in build-artifacts"
    exit 1
  fi
  echo "[INFO] Found dictionary: ${DICT_FILE}"
  cp "${DICT_FILE}" "${INSTALL_DIR}/dict/"

  # Copy integration tests (path defined in pico2.yml test-scripts)
  # These should be in the working directory after extraction
  for test_script in $(find . -path ./lib -prune -o -path ./build-artifacts -prune -o -name "*_integration_tests.py" -print -o -name "test_*.py" -print 2>/dev/null); do
    if [ -f "${test_script}" ]; then
      cp "${test_script}" "${INSTALL_DIR}/test/"
    fi
  done

  # Copy any int test directory if it exists
  if [ -d "artifacts/int" ]; then
    cp -r artifacts/int/. "${INSTALL_DIR}/test/"
  fi

elif [[ "${PLATFORM}" == "pico2" ]]; then
  # Pico 2 (Zephyr) artifacts - legacy custom flow
  echo "[INFO] Setting up Pico 2 (Zephyr) artifacts"
  cp artifacts/build-artifacts/zephyr.hex "${INSTALL_DIR}/bin/"
  cp artifacts/build-artifacts/zephyr/fprime-zephyr-deployment/dict/*TopologyDictionary.json "${INSTALL_DIR}/dict/" 2>/dev/null || \
    cp artifacts/build-artifacts/zephyr/dict/*TopologyDictionary.json "${INSTALL_DIR}/dict/"

  if [ -d "artifacts/int" ]; then
    cp -r artifacts/int/. "${INSTALL_DIR}/test/"
  fi
else
  # Standard Linux artifacts
  echo "[INFO] Setting up Linux artifacts"
  cp artifacts/build-artifacts/*/*/dict/*TopologyDictionary.json "${INSTALL_DIR}/dict/"
  cp artifacts/fprime-gds.yml "${INSTALL_DIR}/fprime-gds.yml" 2>/dev/null || true
  cp -r artifacts/int/. "${INSTALL_DIR}/test/"
  cp artifacts/build-artifacts/*/*/bin/* "${INSTALL_DIR}/bin/fsw"
  chmod +x "${INSTALL_DIR}/bin/fsw"
fi

DICT_PATH=$(ls "${INSTALL_DIR}/dict/"*TopologyDictionary.json | head -n 1)

python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install fprime-gds

echo "[INFO] Soak Setup Complete: ${INSTALL_DIR}"

# Setup and start GDS service on this runner
sudo systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true

# Select appropriate GDS service template based on platform
if [[ "${PLATFORM}" == "pico2" ]] || [[ "${PLATFORM}" == "pico2-fprime-ci" ]]; then
  GDS_TEMPLATE="${TEMPLATES}/gds-pico2.service.template"
else
  GDS_TEMPLATE="${TEMPLATES}/gds.service.template"
fi

render "${GDS_TEMPLATE}" \
| sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

# Verify GDS service started
sudo systemctl is-active --quiet "${SERVICE_NAME}" && { echo "[INFO] ${SERVICE_NAME} is active"; exit 0; }
echo "::error::${SERVICE_NAME} failed to start"
sudo systemctl status "${SERVICE_NAME}" --no-pager -l || true
sudo journalctl -u "${SERVICE_NAME}" --no-pager -n 40 || true
exit 1
