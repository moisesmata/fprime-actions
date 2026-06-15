#!/usr/bin/env bash
#
# Linux/systemd deploy: render unit files from templates, then enable and
# (re)start them. Sudo only for systemctl, journalctl, and writes into
# /etc/systemd/system/.
set -euo pipefail

INSTALL_DIR="${HOME}/fprime-soak"
TEMPLATES="${ACTION_PATH}/templates"

render() {
  sed -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
      -e "s#__SERVICE_USER__#$(whoami)#g" \
      -e "s#__FSW_ARGS__#${FSW_ARGS}#g" "$1"
}

# Setup and start FSW
sudo systemctl disable --now "fprime-soak-fsw" 2>/dev/null || true
render "${TEMPLATES}/fsw.service.template" \
| sudo tee "/etc/systemd/system/fprime-soak-fsw.service" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now fprime-soak-fsw

# Verify FSW started
sudo systemctl is-active --quiet "fprime-soak-fsw" && { echo "[INFO] fprime-soak-fsw is active"; exit 0; }
echo "::error::fprime-soak-fsw failed to start"
sudo systemctl status "fprime-soak-fsw" --no-pager -l || true
sudo journalctl -u "fprime-soak-fsw" --no-pager -n 40 || true
exit 1
