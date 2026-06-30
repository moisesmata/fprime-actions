#!/usr/bin/env bash
#
# Linux/systemd remote deploy: deploys FSW binary and systemd service to a
# remote Pi via SSH, while GDS runs locally on the runner.
#
# Requires:
#   - FSW_HOST: SSH target (e.g. 'pi@192.168.1.100')
#   - FSW_IP: IP address where FSW will listen
#   - FSW_ARGS: arguments for the FSW binary
#   - ACTION_PATH: path to the action directory (for templates)

set -euo pipefail

# Validate required environment variables
if [ -z "${DEPLOYMENT_NAME:-}" ]; then
  echo "::error::DEPLOYMENT_NAME is required"
  exit 1
fi

if [ -z "${FSW_HOST:-}" ]; then
  echo "::error::FSW_HOST is required for linux-remote platform"
  exit 1
fi

if [ -z "${FSW_IP:-}" ]; then
  echo "::error::FSW_IP is required for linux-remote platform"
  exit 1
fi

# Namespace by deployment name
INSTALL_DIR="${HOME}/fprime-soak-${DEPLOYMENT_NAME}"
TEMPLATES="${ACTION_PATH}/templates"
REMOTE_INSTALL_DIR="/home/$(echo "${FSW_HOST}" | cut -d@ -f1)/fprime-soak-${DEPLOYMENT_NAME}"
SERVICE_NAME="fprime-soak-fsw-${DEPLOYMENT_NAME}"

render() {
  sed -e "s#__INSTALL_DIR__#${REMOTE_INSTALL_DIR}#g" \
      -e "s#__SERVICE_USER__#$(echo "${FSW_HOST}" | cut -d@ -f1)#g" \
      -e "s#__FSW_ARGS__#${FSW_ARGS}#g" "$1"
}

echo "[INFO] Deploying FSW to remote host: ${FSW_HOST}"

# Stop FSW service first to release any file locks
ssh "${FSW_HOST}" "sudo systemctl stop ${SERVICE_NAME} 2>/dev/null || true"

# Clean up and recreate remote directories with correct ownership
ssh "${FSW_HOST}" "sudo rm -rf ${REMOTE_INSTALL_DIR} && mkdir -p ${REMOTE_INSTALL_DIR}/bin"

# Copy FSW binary to remote Pi
echo "[INFO] Copying FSW binary to ${FSW_HOST}"
scp "${INSTALL_DIR}/bin/fsw" "${FSW_HOST}:${REMOTE_INSTALL_DIR}/bin/fsw"
ssh "${FSW_HOST}" "chmod +x ${REMOTE_INSTALL_DIR}/bin/fsw"

# Render and deploy systemd service file
echo "[INFO] Deploying systemd service to ${FSW_HOST}"
render "${TEMPLATES}/fsw-remote.service.template" > /tmp/${SERVICE_NAME}.service
scp /tmp/${SERVICE_NAME}.service "${FSW_HOST}:/tmp/${SERVICE_NAME}.service"
ssh "${FSW_HOST}" "sudo mv /tmp/${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service"

# Stop old service, reload systemd, enable and start new service
echo "[INFO] Starting FSW service on ${FSW_HOST}"
ssh "${FSW_HOST}" "sudo systemctl disable --now ${SERVICE_NAME} 2>/dev/null || true"
ssh "${FSW_HOST}" "sudo systemctl daemon-reload"
ssh "${FSW_HOST}" "sudo systemctl enable --now ${SERVICE_NAME}"

# Verify FSW started on remote Pi
echo "[INFO] Verifying FSW service on ${FSW_HOST}"
if ssh "${FSW_HOST}" "sudo systemctl is-active --quiet ${SERVICE_NAME}"; then
  echo "[INFO] ${SERVICE_NAME} is active on ${FSW_HOST}"
  exit 0
fi

echo "::error::${SERVICE_NAME} failed to start on ${FSW_HOST}"
ssh "${FSW_HOST}" "sudo systemctl status ${SERVICE_NAME} --no-pager -l" || true
ssh "${FSW_HOST}" "sudo journalctl -u ${SERVICE_NAME} --no-pager -n 40" || true
exit 1
