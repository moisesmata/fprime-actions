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

# Poll the port itself so a downstream test step doesn't race the FSW.
echo "[INFO] Waiting for FSW to bind 127.0.0.1:50000"
for i in $(seq 1 90); do
  if ss -ltn 'sport = :50000' | grep -q LISTEN; then
    echo "[INFO] FSW listening on 50000 (after ${i}s)"
    break
  fi

  # Check if the fsw failed on startup
  if ! sudo systemctl is-active --quiet fprime-soak-fsw; then
    echo "::error::FSW exited during startup"
    sudo journalctl -u fprime-soak-fsw --no-pager -n 60 || true
    exit 1
  fi
  # Timeout
  if [ "$i" -eq 90 ]; then
    echo "::error::FSW never bound port 50000 within 90s"
    sudo journalctl -u fprime-soak-fsw --no-pager -n 60 || true
    exit 1
  fi
  sleep 1
done

for svc in fprime-soak-fsw fprime-soak-gds; do
  sudo systemctl is-active --quiet "${svc}" && { echo "[INFO] ${svc} is active"; continue; }
  echo "::error::${svc} failed to start"
  sudo systemctl status "${svc}" --no-pager -l || true
  sudo journalctl -u "${svc}" --no-pager -n 40 || true
  exit 1
done
