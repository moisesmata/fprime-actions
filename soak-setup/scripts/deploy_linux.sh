#!/usr/bin/env bash
#
# Linux/systemd deploy: render FSW unit file, enable and start it locally.
# (FSW and GDS both run on the same machine.)
#
# Required environment:
#   DEPLOYMENT_NAME : unique deployment name for namespacing
#   ACTION_PATH     : path to the soak-setup action (provides templates/)
#   FSW_ARGS        : arguments passed to the FSW binary

set -euo pipefail

INSTALL_DIR="${HOME}/fprime-soak-${DEPLOYMENT_NAME}"
TEMPLATES="${ACTION_PATH}/templates"
SERVICE_NAME="fprime-soak-fsw-${DEPLOYMENT_NAME}"

render() {
  sed -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
      -e "s#__SERVICE_USER__#$(whoami)#g" \
      -e "s#__FSW_ARGS__#${FSW_ARGS}#g" "$1"
}

sudo systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true
render "${TEMPLATES}/fsw.service.template" \
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
