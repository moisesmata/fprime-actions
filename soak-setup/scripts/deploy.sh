#!/usr/bin/env bash
#
# Platform-dispatched deploy step.
#   linux         : start FSW locally
#   linux-remote  : scp binary + rendered service to the FSW Pi over SSH, start it.
#                   Remote user is 'fprime' with passwordless sudo for systemctl.
#   pico2         : fprime-ci already flashed the board; verify the serial
#                   device is back.

set -euo pipefail

if [[ "${PLATFORM}" == "pico2" ]]; then
  FSW_DEVICE="${FSW_DEVICE:-/dev/pico2}"
  for i in $(seq 1 10); do
    if [ -e "${FSW_DEVICE}" ]; then
      echo "[INFO] Serial device ${FSW_DEVICE} detected"
      exit 0
    fi
    echo "[INFO] Waiting for ${FSW_DEVICE} (${i}/10)..."
    sleep 1
  done
  echo "::warning::Serial device ${FSW_DEVICE} not found"
  exit 0
fi

# linux vs linux-remote: same systemd flow; run() prefixes SSH when remote.
FSW_USER="fprime"
LOCAL_INSTALL_DIR="${HOME}/fprime-soak-${DEPLOYMENT_NAME}"
TEMPLATES="${ACTION_PATH}/templates"
SERVICE_NAME="fprime-soak-fsw-${DEPLOYMENT_NAME}"
UNIT_TMP="/tmp/${SERVICE_NAME}.service"

if [[ "${PLATFORM}" == "linux-remote" ]]; then
  FSW_HOST="${FSW_USER}@${FSW_IP}"
  INSTALL_DIR="/home/${FSW_USER}/fprime-soak-${DEPLOYMENT_NAME}"
  SERVICE_USER="${FSW_USER}"
  FSW_ARGS="-a 0.0.0.0 -p ${FSW_PORT:-50000}"
  run() { ssh "${FSW_HOST}" "$*"; }
  ship() { scp "$1" "${FSW_HOST}:$2" >/dev/null; }
  run "sudo rm -rf ${INSTALL_DIR} && mkdir -p ${INSTALL_DIR}/bin"
  ship "${LOCAL_INSTALL_DIR}/bin/fsw" "${INSTALL_DIR}/bin/fsw"
  run "chmod +x ${INSTALL_DIR}/bin/fsw"
else
  INSTALL_DIR="${LOCAL_INSTALL_DIR}"
  SERVICE_USER="$(whoami)"
  FSW_ARGS="${FSW_ARGS:--a 127.0.0.1 -p 50000}"
  run() { bash -c "$*"; }
  ship() { :; }  # unit file already at destination locally
fi

sed -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
    -e "s#__SERVICE_USER__#${SERVICE_USER}#g" \
    -e "s#__FSW_ARGS__#${FSW_ARGS}#g" \
    "${TEMPLATES}/fsw.service.template" > "${UNIT_TMP}"
ship "${UNIT_TMP}" "${UNIT_TMP}"
run "sudo mv ${UNIT_TMP} /etc/systemd/system/${SERVICE_NAME}.service"

run "sudo systemctl disable --now ${SERVICE_NAME} 2>/dev/null || true"
run "sudo systemctl daemon-reload"
run "sudo systemctl enable --now ${SERVICE_NAME}"

if run "sudo systemctl is-active --quiet ${SERVICE_NAME}"; then
  echo "[INFO] ${SERVICE_NAME} is active"
  exit 0
fi

echo "::error::${SERVICE_NAME} failed to start"
run "sudo systemctl status ${SERVICE_NAME} --no-pager -l" || true
run "sudo journalctl -u ${SERVICE_NAME} --no-pager -n 40" || true
exit 1
