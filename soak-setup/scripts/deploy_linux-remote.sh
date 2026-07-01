#!/usr/bin/env bash
#
# Linux/systemd remote deploy: copy the FSW binary + service unit to the remote
# Pi via SSH and start it. GDS runs locally on this runner.
#
# Assumption: the remote Pi has a user named 'fprime' with passwordless sudo
# for systemctl, and SSH key auth set up from this runner.
#
# Required environment:
#   DEPLOYMENT_NAME : unique deployment name
#   FSW_IP          : IP address of the remote FSW Pi
#   FSW_PORT        : port FSW listens on (default 50000)
#   ACTION_PATH     : path to the soak-setup action (provides templates/)

set -euo pipefail

FSW_USER="fprime"
FSW_HOST="${FSW_USER}@${FSW_IP}"
FSW_PORT="${FSW_PORT:-50000}"
FSW_ARGS="-a 0.0.0.0 -p ${FSW_PORT}"

INSTALL_DIR="${HOME}/fprime-soak-${DEPLOYMENT_NAME}"
TEMPLATES="${ACTION_PATH}/templates"
REMOTE_INSTALL_DIR="/home/${FSW_USER}/fprime-soak-${DEPLOYMENT_NAME}"
SERVICE_NAME="fprime-soak-fsw-${DEPLOYMENT_NAME}"

render() {
  sed -e "s#__INSTALL_DIR__#${REMOTE_INSTALL_DIR}#g" \
      -e "s#__SERVICE_USER__#${FSW_USER}#g" \
      -e "s#__FSW_ARGS__#${FSW_ARGS}#g" "$1"
}

echo "[INFO] Deploying FSW to remote host: ${FSW_HOST}"

# Stop FSW service first to release any file locks on the binary
ssh "${FSW_HOST}" "sudo systemctl stop ${SERVICE_NAME} 2>/dev/null || true"

# Clean and recreate the remote install dir
ssh "${FSW_HOST}" "sudo rm -rf ${REMOTE_INSTALL_DIR} && mkdir -p ${REMOTE_INSTALL_DIR}/bin"

# Copy binary
echo "[INFO] Copying FSW binary to ${FSW_HOST}"
scp "${INSTALL_DIR}/bin/fsw" "${FSW_HOST}:${REMOTE_INSTALL_DIR}/bin/fsw"
ssh "${FSW_HOST}" "chmod +x ${REMOTE_INSTALL_DIR}/bin/fsw"

# Render and install systemd unit
echo "[INFO] Installing systemd unit on ${FSW_HOST}"
render "${TEMPLATES}/fsw-remote.service.template" > "/tmp/${SERVICE_NAME}.service"
scp "/tmp/${SERVICE_NAME}.service" "${FSW_HOST}:/tmp/${SERVICE_NAME}.service"
ssh "${FSW_HOST}" "sudo mv /tmp/${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service"

# Start service
echo "[INFO] Starting ${SERVICE_NAME} on ${FSW_HOST}"
ssh "${FSW_HOST}" "sudo systemctl disable --now ${SERVICE_NAME} 2>/dev/null || true"
ssh "${FSW_HOST}" "sudo systemctl daemon-reload"
ssh "${FSW_HOST}" "sudo systemctl enable --now ${SERVICE_NAME}"

if ssh "${FSW_HOST}" "sudo systemctl is-active --quiet ${SERVICE_NAME}"; then
  echo "[INFO] ${SERVICE_NAME} is active on ${FSW_HOST}"
  exit 0
fi

echo "::error::${SERVICE_NAME} failed to start on ${FSW_HOST}"
ssh "${FSW_HOST}" "sudo systemctl status ${SERVICE_NAME} --no-pager -l" || true
ssh "${FSW_HOST}" "sudo journalctl -u ${SERVICE_NAME} --no-pager -n 40" || true
exit 1
