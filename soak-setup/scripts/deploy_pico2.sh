#!/usr/bin/env bash
#
# Pico 2 deploy: fprime-ci already flashed the Pico 2 via OpenOCD in a prior
# step. This script just verifies the serial device is back online.
#
# Required environment:
#   DEPLOYMENT_NAME : unique deployment name (required by action.yml, unused here)
#   FSW_DEVICE      : serial device path (default /dev/pico2)

set -euo pipefail

if [ -z "${DEPLOYMENT_NAME:-}" ]; then
  echo "::error::DEPLOYMENT_NAME is required"
  exit 1
fi

FSW_DEVICE="${FSW_DEVICE:-/dev/pico2}"

echo "[INFO] Verifying Pico 2 serial device after flash"

for i in $(seq 1 10); do
  if [ -e "${FSW_DEVICE}" ]; then
    echo "[INFO] Serial device ${FSW_DEVICE} detected"
    exit 0
  fi
  echo "[INFO] Waiting for ${FSW_DEVICE} (${i}/10)..."
  sleep 1
done

echo "::warning::Serial device ${FSW_DEVICE} not found"
echo "Check udev rules and USB connection. Try: dmesg | grep tty"
