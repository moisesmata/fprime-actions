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
      -e "s#__GDS_ARGS__#${GDS_ARGS}#g" "$1"
}

for unit in fsw gds; do
  sudo systemctl disable --now "fprime-soak-${unit}" 2>/dev/null || true
  render "${TEMPLATES}/${unit}.service" \
    | sudo tee "/etc/systemd/system/fprime-soak-${unit}.service" >/dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now fprime-soak-fsw fprime-soak-gds
sleep 5

for svc in fprime-soak-fsw fprime-soak-gds; do
  sudo systemctl is-active --quiet "${svc}" && { echo "[INFO] ${svc} is active"; continue; }
  echo "::error::${svc} failed to start"
  sudo systemctl status "${svc}" --no-pager -l || true
  sudo journalctl -u "${svc}" --no-pager -n 40 || true
  exit 1
done
