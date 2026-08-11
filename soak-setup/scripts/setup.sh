#!/usr/bin/env bash
#
# Soak setup: stage build artifacts into $HOME/fprime-soak-<deployment>,
# build the soak virtualenv, and start the persistent GDS service.

set -euo pipefail

PLATFORM="${PLATFORM}"
INSTALL_DIR="${HOME}/fprime-soak-${DEPLOYMENT_NAME}"
TEMPLATES="${ACTION_PATH}/templates"
SERVICE_NAME="fprime-soak-gds-${DEPLOYMENT_NAME}"
ZMQ_TRANSPORT="--zmq-transport ipc:///tmp/fprime-server-${DEPLOYMENT_NAME}-in ipc:///tmp/fprime-server-${DEPLOYMENT_NAME}-out"

# Delete previous soak install directory
rm -rf "${INSTALL_DIR}"

mkdir -p "${INSTALL_DIR}"/{bin,dict,gds-logs,ComLoggerFiles,test}
echo "# SOAK STARTED $(date +%Y-%m-%dT%H:%M:%S)" > "${INSTALL_DIR}/soak.log"

case "${PLATFORM}" in
  linux|linux-remote)
    echo "[INFO] Staging Linux artifacts"
    cp artifacts/build-artifacts/*/*/dict/*TopologyDictionary.json "${INSTALL_DIR}/dict/"
    cp artifacts/fprime-gds.yml "${INSTALL_DIR}/fprime-gds.yml" 2>/dev/null || true
    cp -r artifacts/int/. "${INSTALL_DIR}/test/"
    cp artifacts/build-artifacts/*/*/bin/* "${INSTALL_DIR}/bin/fsw"
    chmod +x "${INSTALL_DIR}/bin/fsw"
    ;;
  pico2)
    # fprime-ci archives ./build-artifacts (plus dictionary/executable/test-scripts
    # by basename) into archive.tar.gz.
    echo "[INFO] Staging Pico 2 artifacts from fprime-ci build"
    echo "[INFO] Extracting archive.tar.gz"
    tar -xzf ./archive.tar.gz
    cp ./build-artifacts/*/*/dict/*TopologyDictionary.json "${INSTALL_DIR}/dict/"
    cp -r ./*/*/test/int/. "${INSTALL_DIR}/test/" 2>/dev/null || true
    cp ./fprime-gds.yml "${INSTALL_DIR}/fprime-gds.yml" 2>/dev/null || true
    ;;
  *)
    echo "::error::Unknown platform: ${PLATFORM}"
    exit 1
    ;;
esac

GDS_ARGS="${ZMQ_TRANSPORT} ${GDS_ARGS:-}"

DICT_PATH=$(ls "${INSTALL_DIR}/dict/"*TopologyDictionary.json | head -n 1)

python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install fprime-gds

echo "[INFO] Soak Setup Complete: ${INSTALL_DIR}"

sudo systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true
sed -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
    -e "s#__SERVICE_USER__#$(whoami)#g" \
    -e "s#__GDS_ARGS__#${GDS_ARGS}#g" \
    -e "s#__DICT_PATH__#${DICT_PATH}#g" \
    "${TEMPLATES}/gds.service.template" \
  | sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

if sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
  echo "[INFO] ${SERVICE_NAME} is active"
  exit 0
fi

echo "::error::${SERVICE_NAME} failed to start"
sudo systemctl status "${SERVICE_NAME}" --no-pager -l || true
sudo journalctl -u "${SERVICE_NAME}" --no-pager -n 40 || true
exit 1
