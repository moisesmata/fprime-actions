#!/usr/bin/env bash
#
# Cleanup legacy (non-namespaced) soak test installations
#
# Run this on both Pi 1 (GDS runner) and Pi 2 (FSW) to remove old services
# before switching to namespaced deployments.

set -euo pipefail

echo "[INFO] Cleaning up legacy soak test services"

# Stop and remove GDS service (on Pi 1)
if systemctl list-unit-files | grep -q "fprime-soak-gds.service"; then
  echo "[INFO] Stopping fprime-soak-gds service"
  sudo systemctl disable --now fprime-soak-gds 2>/dev/null || true
  sudo rm -f /etc/systemd/system/fprime-soak-gds.service
fi

# Stop and remove FSW service (on Pi 2, or if running locally)
if systemctl list-unit-files | grep -q "fprime-soak-fsw.service"; then
  echo "[INFO] Stopping fprime-soak-fsw service"
  sudo systemctl disable --now fprime-soak-fsw 2>/dev/null || true
  sudo rm -f /etc/systemd/system/fprime-soak-fsw.service
fi

# Reload systemd
sudo systemctl daemon-reload

# Remove old installation directory
if [ -d "${HOME}/fprime-soak" ]; then
  echo "[INFO] Removing ${HOME}/fprime-soak"
  rm -rf "${HOME}/fprime-soak"
fi

# Remove old ZMQ sockets
if [ -e "/tmp/fprime-server-in" ] || [ -e "/tmp/fprime-server-out" ]; then
  echo "[INFO] Removing old ZMQ sockets"
  rm -f /tmp/fprime-server-in /tmp/fprime-server-out
fi

echo "[INFO] Legacy cleanup complete"
echo ""
echo "You can now run the new namespaced soak setup workflows."
echo "Make sure your workflows include the 'deployment-name' parameter!"
