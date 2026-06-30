#!/usr/bin/env bash
#
# Pico 2/Zephyr deploy: flash FSW to Pico 2 via OpenOCD
#
# Required environment variables:
#   DEPLOYMENT_NAME: Deployment name for namespacing
#   FSW_HEX (optional): Path to hex file, defaults to ${INSTALL_DIR}/bin/zephyr.hex
#   FSW_DEVICE (optional): Serial device path, defaults to /dev/pico2
#   OPENOCD_CFG (optional): OpenOCD config file, defaults to interface/cmsis-dap.cfg

set -euo pipefail

# Validate required environment variables
if [ -z "${DEPLOYMENT_NAME:-}" ]; then
  echo "::error::DEPLOYMENT_NAME is required"
  exit 1
fi

# Namespace by deployment name
INSTALL_DIR="${HOME}/fprime-soak-${DEPLOYMENT_NAME}"
FSW_HEX="${FSW_HEX:-${INSTALL_DIR}/bin/zephyr.hex}"
FSW_DEVICE="${FSW_DEVICE:-/dev/pico2}"

echo "[INFO] Flashing Pico 2 FSW"

# Verify hex file exists
if [ ! -f "${FSW_HEX}" ]; then
  echo "::error::FSW hex file not found: ${FSW_HEX}"
  exit 1
fi

echo "[INFO] Using hex file: ${FSW_HEX}"

# Check if OpenOCD is installed
if ! command -v openocd &> /dev/null; then
  echo "::error::OpenOCD not found. Install with: sudo apt-get install openocd"
  exit 1
fi

# Flash via OpenOCD
echo "[INFO] Flashing to Pico 2 via OpenOCD"
openocd \
  -f interface/cmsis-dap.cfg \
  -f target/rp2350.cfg \
  -c 'adapter speed 5000' \
  -c init \
  -c targets \
  -c 'reset init' \
  -c "flash write_image erase ${FSW_HEX}" \
  -c 'reset run' \
  -c shutdown

flash_result=$?

if [ ${flash_result} -ne 0 ]; then
  echo "::error::OpenOCD flash failed with exit code ${flash_result}"
  echo "Check that:"
  echo "  - Debug probe is connected to Pico 2 SWD pins"
  echo "  - Pico 2 has power (VBUS or VSYS)"
  echo "  - udev rules allow access to debug probe"
  exit 1
fi

echo "[INFO] Flash successful, waiting for USB enumeration..."
sleep 3

# Verify serial device exists
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
  echo "::warning::Serial device ${FSW_DEVICE} not found after flash"
  echo "FSW may still be running. Check with: dmesg | grep tty"
  echo "You may need to adjust udev rules or device path"
fi

echo "[INFO] Pico 2 deployment complete"
