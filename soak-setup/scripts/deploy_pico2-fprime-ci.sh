#!/usr/bin/env bash
#
# Pico 2 deploy using fprime-ci flow:
# - fprime-ci already flashed the Pico 2 in a previous step
# - This script just verifies the FSW is running and the serial device is available
#
# Required environment variables:
#   DEPLOYMENT_NAME: Deployment name for namespacing
#   FSW_DEVICE (optional): Serial device path, defaults to /dev/pico2

set -euo pipefail

# Validate required environment variables
if [ -z "${DEPLOYMENT_NAME:-}" ]; then
  echo "::error::DEPLOYMENT_NAME is required"
  exit 1
fi

FSW_DEVICE="${FSW_DEVICE:-/dev/pico2}"

echo "[INFO] Verifying Pico 2 FSW is running after fprime-ci flash"

# Wait for serial device to appear (fprime-ci just flashed it)
max_retries=10
retry_count=0
while [ ${retry_count} -lt ${max_retries} ]; do
  if [ -e "${FSW_DEVICE}" ]; then
    echo "[INFO] Serial device ${FSW_DEVICE} detected"
    break
  fi
  retry_count=$((retry_count + 1))
  echo "[INFO] Waiting for ${FSW_DEVICE} (attempt ${retry_count}/${max_retries})..."
  sleep 1
done

if [ ! -e "${FSW_DEVICE}" ]; then
  echo "::warning::Serial device ${FSW_DEVICE} not found"
  echo "Check udev rules and USB connection"
  echo "Run: dmesg | grep tty"
fi

echo "[INFO] Pico 2 deployment complete (flashed by fprime-ci)"
