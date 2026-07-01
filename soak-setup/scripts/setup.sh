#!/usr/bin/env bash
#
# Soak setup: stage build artifacts into $HOME/fprime-soak-<deployment>,
# build the soak virtualenv, and start the persistent GDS service.
#
# Required environment:
#   DEPLOYMENT_NAME : unique deployment name (namespaces install dir, service, sockets)
#   PLATFORM        : "linux" | "linux-remote" | "pico2"
#   ACTION_PATH     : path to the soak-setup action (provides templates/)
#   GDS_ARGS        : extra args appended to fprime-gds
#   FSW_IP          : IP of remote FSW (linux-remote only)
#   FSW_PORT        : FSW TCP port (linux-remote only, default 50000)
#   FSW_DEVICE      : serial device (pico2 only, default /dev/pico2)

set -euo pipefail

PLATFORM="${PLATFORM:-linux}"
INSTALL_DIR="${HOME}/fprime-soak-${DEPLOYMENT_NAME}"
TEMPLATES="${ACTION_PATH}/templates"
SERVICE_NAME="fprime-soak-gds-${DEPLOYMENT_NAME}"
ZMQ_TRANSPORT="--zmq-transport ipc:///tmp/fprime-server-${DEPLOYMENT_NAME}-in ipc:///tmp/fprime-server-${DEPLOYMENT_NAME}-out"
EXTRA_SERVICE=""

rm -rf "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"/{bin,dict,gds-logs,ComLoggerFiles,test}
echo "# SOAK STARTED $(date +%Y-%m-%dT%H:%M:%S)" > "${INSTALL_DIR}/soak.log"

case "${PLATFORM}" in
  linux)
    echo "[INFO] Staging Linux artifacts"
    cp artifacts/build-artifacts/*/*/dict/*TopologyDictionary.json "${INSTALL_DIR}/dict/"
    cp artifacts/fprime-gds.yml "${INSTALL_DIR}/fprime-gds.yml" 2>/dev/null || true
    cp -r artifacts/int/. "${INSTALL_DIR}/test/"
    cp artifacts/build-artifacts/*/*/bin/* "${INSTALL_DIR}/bin/fsw"
    chmod +x "${INSTALL_DIR}/bin/fsw"
    GDS_ARGS="${ZMQ_TRANSPORT} ${GDS_ARGS:-}"
    ;;
  linux-remote)
    echo "[INFO] Staging Linux artifacts"
    cp artifacts/build-artifacts/*/*/dict/*TopologyDictionary.json "${INSTALL_DIR}/dict/"
    cp artifacts/fprime-gds.yml "${INSTALL_DIR}/fprime-gds.yml" 2>/dev/null || true
    cp -r artifacts/int/. "${INSTALL_DIR}/test/"
    cp artifacts/build-artifacts/*/*/bin/* "${INSTALL_DIR}/bin/fsw"
    chmod +x "${INSTALL_DIR}/bin/fsw"
    GDS_ARGS="--communication-selection ip --ip-address ${FSW_IP} --ip-port ${FSW_PORT:-50000} --ip-client ${ZMQ_TRANSPORT} ${GDS_ARGS:-}"
    ;;
  pico2)
    # fprime-ci already extracted archive.tar.gz to ./build-artifacts/
    echo "[INFO] Staging Pico 2 artifacts from fprime-ci build"
    DICT_FILE=$(find ./build-artifacts -name "*TopologyDictionary.json" | head -n 1)
    cp "${DICT_FILE}" "${INSTALL_DIR}/dict/"
    INT_TEST_DIR=$(find . -path ./lib -prune -o -type d -name "int" -print 2>/dev/null | grep -v "^./build-artifacts" | head -n 1)
    GDS_ARGS="--communication-selection uart --uart-device ${FSW_DEVICE:-/dev/pico2} --uart-baud 115200 --uart-skip-port-check ${ZMQ_TRANSPORT} ${GDS_ARGS:-}"
    EXTRA_SERVICE="SupplementaryGroups=dialout"
    ;;
  *)
    echo "::error::Unknown platform: ${PLATFORM}"
    exit 1
    ;;
esac

DICT_PATH=$(ls "${INSTALL_DIR}/dict/"*TopologyDictionary.json | head -n 1)

python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install fprime-gds

echo "[INFO] Soak Setup Complete: ${INSTALL_DIR}"

sudo systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true
sed -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
    -e "s#__SERVICE_USER__#$(whoami)#g" \
    -e "s#__GDS_ARGS__#${GDS_ARGS}#g" \
    -e "s#__DICT_PATH__#${DICT_PATH}#g" \
    -e "s#__EXTRA_SERVICE__#${EXTRA_SERVICE}#g" \
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
