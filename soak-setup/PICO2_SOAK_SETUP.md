# Raspberry Pi Pico 2 Soak Test Setup Guide

This guide explains how to set up soak testing for F´ Zephyr deployments on the Raspberry Pi Pico 2, using a Raspberry Pi as the GDS runner with UART connection to the Pico 2.

## Architecture Overview

- **Pi (GDS Runner)**: Runs GitHub Actions runner, GDS, and integration tests
- **Pico 2 (FSW Target)**: Runs F´ Zephyr flight software binary
- **Connection**: UART (USB serial) for both console logs and GDS communication
- **Flashing**: OpenOCD via SWD (using picoprobe or Raspberry Pi Debug Probe)

---

## Hardware Requirements

1. **Raspberry Pi** (any model, ARM64 recommended for performance)
   - Running Ubuntu or Raspberry Pi OS
   - GitHub Actions self-hosted runner installed
   
2. **Raspberry Pi Pico 2**
   - Target board running F´ Zephyr FSW
   
3. **Debug Probe** (one of):
   - **Option A**: Raspberry Pi Debug Probe (recommended, official)
   - **Option B**: Another Pico/Pico 2 as Picoprobe
   - **Option C**: Segger J-Link, ST-Link, or other SWD debugger
   
4. **Connections**:
   - **USB-Serial**: Pico 2 USB port → Pi USB port (for FSW UART communication)
   - **SWD**: Debug probe → Pico 2 SWD pins (for flashing)
   - **Power**: Pico 2 VBUS from USB or external 5V

---

## Physical Wiring

### Pico 2 Pinout for SWD

```
Pico 2 Debug Header:
┌─────────────────┐
│  1  2  3        │  1: GND
│ GND SWCLK SWDIO │  2: SWCLK (GP2 when using Picoprobe)
└─────────────────┘  3: SWDIO (GP3 when using Picoprobe)
```

### Option A: Using Raspberry Pi Debug Probe

```
Debug Probe → Pico 2
  GND       → GND (Pin 1)
  SWCLK     → SWCLK (Pin 2) 
  SWDIO     → SWDIO (Pin 3)
  
USB Cable → Pi USB port
```

### Option B: Using Another Pico as Picoprobe

Flash picoprobe firmware on the second Pico:
```bash
# Download picoprobe firmware
wget https://github.com/raspberrypi/picoprobe/releases/latest/download/picoprobe.uf2

# Put Pico in BOOTSEL mode (hold BOOTSEL, plug USB)
# Copy picoprobe.uf2 to the Pico drive
```

Wiring:
```
Picoprobe Pico → Target Pico 2
  GP2 (Pin 4)   → SWCLK
  GP3 (Pin 5)   → SWDIO  
  GND (Pin 3)   → GND
```

---

## Pi (GDS Runner) Setup

### 1. Install Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
    python3 python3-pip python3-venv \
    openocd \
    udev \
    git

# Install fprime-gds (in workflow, but useful for testing)
python3 -m venv ~/test-venv
~/test-venv/bin/pip install fprime-gds
```

### 2. Configure OpenOCD

Check OpenOCD version (needs 0.12.0+ for RP2350 support):
```bash
openocd --version
```

If older, build from source:
```bash
git clone https://github.com/raspberrypi/openocd.git --branch rp2350 --depth=1
cd openocd
./bootstrap
./configure --enable-cmsis-dap
make -j4
sudo make install
```

### 3. Configure udev Rules

Create `/etc/udev/rules.d/99-pico.rules`:
```bash
sudo tee /etc/udev/rules.d/99-pico.rules <<EOF
# Raspberry Pi Pico 2 USB serial
SUBSYSTEM=="tty", ATTRS{idVendor}=="2e8a", ATTRS{idProduct}=="000a", SYMLINK+="pico2", MODE="0666"

# Raspberry Pi Debug Probe
SUBSYSTEM=="usb", ATTR{idVendor}=="2e8a", ATTR{idProduct}=="000c", MODE="0666"

# CMSIS-DAP (Picoprobe)
SUBSYSTEM=="usb", ATTR{idVendor}=="2e8a", ATTR{idProduct}=="0004", MODE="0666"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
```

This creates a stable `/dev/pico2` symlink for the UART port.

### 4. Test UART Connection

```bash
# Plug in Pico 2 via USB
ls -l /dev/pico2
# Should show: /dev/pico2 -> ttyACM0 (or similar)

# Test reading from serial port
cat /dev/pico2
# Should show boot messages if FSW is running
```

### 5. Test OpenOCD Connection

Create `test-openocd.cfg`:
```
source [find interface/cmsis-dap.cfg]
source [find target/rp2350.cfg]
adapter speed 5000
init
targets
exit
```

Run:
```bash
sudo openocd -f test-openocd.cfg
# Should detect the RP2350 target
```

### 6. GitHub Actions Runner Setup

Install as the user that will run workflows (e.g., `fprime`):
```bash
# Download latest runner
mkdir ~/actions-runner && cd ~/actions-runner
curl -o actions-runner-linux-arm64-2.314.1.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.314.1/actions-runner-linux-arm64-2.314.1.tar.gz
tar xzf ./actions-runner-linux-arm64-*.tar.gz

# Configure
./config.sh --url https://github.com/YOUR_ORG/YOUR_REPO --token YOUR_TOKEN \
  --labels self-hosted,ARM64,pico2-soak

# Install as service
sudo ./svc.sh install
sudo ./svc.sh start
```

---

## Pico 2 Setup

### 1. Flash Zephyr Firmware (Initial)

```bash
# Build the firmware (done by workflow)
cd fprime-zephyr-reference
fprime-util generate -DBOARD=rpi_pico2/rp2350a/m33
fprime-util build --target FprimeZephyrReference_ReferenceDeployment

# Flash via OpenOCD
cd build-artifacts/zephyr
openocd \
  -s ./openocd/tcl \
  -f interface/cmsis-dap.cfg \
  -f target/rp2350.cfg \
  -c 'set_adapter_speed_if_not_set 5000' \
  -c init \
  -c 'reset init' \
  -c 'flash write_image erase zephyr.hex' \
  -c 'reset run' \
  -c shutdown
```

### 2. Verify FSW Boot

```bash
# Monitor serial output
screen /dev/pico2 115200
# or
cat /dev/pico2

# Expected output:
# *** Booting Zephyr OS build v3.x.x ***
# [00:00:00.000] EVENT: ...
```

---

## Workflow Integration

### 1. Create Soak Setup Workflow

Create `.github/workflows/pico2-soak-setup.yml`:

```yaml
name: "Soak Setup: Pico 2 Reference"

on:
  release:
    types: [published]
  workflow_dispatch:

concurrency:
  group: pico2-soak
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  cross-compilation:
    name: "Cross Compilation"
    runs-on: ubuntu-22.04
    steps:
      - name: "Setup external repository + overlay F´"
        uses: nasa/fprime-actions/external-repository-setup@devel
        with:
          target_repository: fprime-community/fprime-zephyr-reference
          target_ref: ${{ github.event.release.tag_name || 'devel' }}
      
      - name: "Install Zephyr SDK"
        uses: nasa/fprime-actions/setup-zephyr@devel
      
      - name: "Cross compile for Pico 2"
        run: |
          fprime-util generate -DBOARD=rpi_pico2/rp2350a/m33
          fprime-util build --target FprimeZephyrReference_ReferenceDeployment
      
      - name: "Stage artifacts for soak"
        run: |
          mkdir -p artifacts
          cp -r build-artifacts artifacts/
          cp -r FprimeZephyrReference/ReferenceDeployment/test/int artifacts/int
          cp lib/zephyr-workspace/zephyr/boards/raspberrypi/rpi_pico2/support/openocd.cfg artifacts/
      
      - name: "Archive soak artifact"
        uses: actions/upload-artifact@v4
        with:
          name: pico2-soak-artifact
          path: artifacts/
          retention-days: 5

  deploy:
    name: "Deploy Soak Services"
    runs-on: [self-hosted, ARM64, pico2-soak]
    needs: cross-compilation
    steps:
      - name: "Download soak artifact"
        uses: actions/download-artifact@v4
        with:
          name: pico2-soak-artifact
          path: artifacts
      
      - name: "Deploy FSW + persistent GDS services"
        uses: YOUR_ORG/fprime-actions/soak-setup@YOUR_BRANCH
        with:
          platform: pico2
          fsw-device: /dev/pico2
          fsw-hex: artifacts/build-artifacts/zephyr.hex
          openocd-cfg: artifacts/openocd.cfg
          gds-args: "--communication-selection uart --uart-device /dev/pico2 --uart-baud 115200 --zmq-transport ipc:///tmp/fprime-server-in ipc:///tmp/fprime-server-out"
```

### 2. Create Soak Test Workflow

Create `.github/workflows/pico2-soak-test.yml`:

```yaml
name: "Soak Test: Pico 2 Reference"

on:
  schedule:
    - cron: "*/30 * * * *"  # Every 30 minutes
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: pico2-soak
  cancel-in-progress: false

jobs:
  soak-test:
    name: "Soak Test"
    runs-on: [self-hosted, ARM64, pico2-soak]
    steps:
      - uses: YOUR_ORG/fprime-actions/soak-test@YOUR_BRANCH
```

---

## fprime-actions Implementation

### 1. Create `deploy_pico2.sh`

Create `fprime-actions/soak-setup/scripts/deploy_pico2.sh`:

```bash
#!/usr/bin/env bash
#
# Pico 2/Zephyr deploy: flash FSW to Pico 2 via OpenOCD
#
set -euo pipefail

INSTALL_DIR="${HOME}/fprime-soak"
FSW_HEX="${FSW_HEX:-${INSTALL_DIR}/bin/zephyr.hex}"
FSW_DEVICE="${FSW_DEVICE:-/dev/pico2}"
OPENOCD_CFG="${OPENOCD_CFG:-${INSTALL_DIR}/openocd.cfg}"

echo "[INFO] Flashing Pico 2 at ${FSW_DEVICE}"

# Verify hex file exists
if [ ! -f "${FSW_HEX}" ]; then
  echo "::error::FSW hex file not found: ${FSW_HEX}"
  exit 1
fi

# Verify device exists
if [ ! -e "${FSW_DEVICE}" ]; then
  echo "::error::Pico 2 device not found: ${FSW_DEVICE}"
  echo "Check USB connection and udev rules"
  exit 1
fi

# Flash via OpenOCD
echo "[INFO] Flashing ${FSW_HEX} to Pico 2"
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

if [ $? -eq 0 ]; then
  echo "[INFO] Flash successful"
else
  echo "::error::Flash failed"
  exit 1
fi

# Wait for Pico to enumerate on USB
echo "[INFO] Waiting for Pico 2 to enumerate..."
sleep 2

# Verify device is back
if [ ! -e "${FSW_DEVICE}" ]; then
  echo "::error::Pico 2 device not found after flash: ${FSW_DEVICE}"
  exit 1
fi

echo "[INFO] Pico 2 deployment complete"
```

### 2. Update `setup.sh` for Pico 2

Modify `fprime-actions/soak-setup/scripts/setup.sh` to handle Zephyr artifacts:

```bash
# Add after existing setup
if [[ "${PLATFORM:-}" == "pico2" ]]; then
  # Pico 2 specific setup
  cp artifacts/build-artifacts/zephyr.hex "${INSTALL_DIR}/bin/"
  cp artifacts/openocd.cfg "${INSTALL_DIR}/"
fi
```

### 3. Update `action.yml`

Add Pico 2 specific inputs:

```yaml
inputs:
  # ... existing inputs ...
  fsw-device:
    description: "Device path for FSW UART (e.g. /dev/pico2)"
    required: false
    default: "/dev/pico2"
  fsw-hex:
    description: "Path to FSW hex file (for Pico 2/Zephyr)"
    required: false
    default: ""
  openocd-cfg:
    description: "Path to OpenOCD config file"
    required: false
    default: ""
```

---

## Pico 2 Service Templates

### FSW Service (No Service Needed)

For Pico 2, the FSW runs directly on the hardware after flashing. No systemd service is needed.

### GDS Service Template

Create `fprime-actions/soak-setup/templates/gds-pico2.service.template`:

```ini
[Unit]
Description=F' soak-test GDS for Pico 2
After=network.target

[Service]
Type=simple
User=__SERVICE_USER__
WorkingDirectory=__INSTALL_DIR__
ExecStart=__INSTALL_DIR__/venv/bin/fprime-gds --no-app --logs __INSTALL_DIR__/gds-logs --dictionary __DICT_PATH__ __GDS_ARGS__
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

# Allow access to /dev/pico2
SupplementaryGroups=dialout

[Install]
WantedBy=multi-user.target
```

---

## Testing the Setup

### 1. Manual Flash Test

```bash
cd ~/fprime-zephyr-reference
fprime-util generate -DBOARD=rpi_pico2/rp2350a/m33
fprime-util build --target FprimeZephyrReference_ReferenceDeployment

# Flash
cd build-artifacts/zephyr
openocd \
  -f interface/cmsis-dap.cfg \
  -f target/rp2350.cfg \
  -c 'adapter speed 5000' \
  -c init \
  -c 'reset init' \
  -c 'flash write_image erase zephyr.hex' \
  -c 'reset run' \
  -c shutdown
```

### 2. Manual GDS Test

```bash
# In one terminal, monitor raw output
cat /dev/pico2

# In another, start GDS
fprime-gds \
  --no-app \
  --dictionary build-artifacts/zephyr/fprime-zephyr-deployment/dict/ReferenceDeploymentTopologyDictionary.json \
  --communication-selection uart \
  --uart-device /dev/pico2 \
  --uart-baud 115200 \
  --zmq-transport ipc:///tmp/fprime-server-in ipc:///tmp/fprime-server-out
```

### 3. Integration Test

```bash
pytest \
  --dictionary build-artifacts/zephyr/fprime-zephyr-deployment/dict/ReferenceDeploymentTopologyDictionary.json \
  --zmq-transport ipc:///tmp/fprime-server-in ipc:///tmp/fprime-server-out \
  FprimeZephyrReference/ReferenceDeployment/test/int/
```

---

## Troubleshooting

### OpenOCD Can't Find Target

```bash
# Check debug probe is connected
lsusb | grep -i "2e8a\|cmsis"

# Check permissions
ls -l /dev/bus/usb/*/*
# Should show mode 0666 for probe

# Try with sudo
sudo openocd -f interface/cmsis-dap.cfg -f target/rp2350.cfg -c init -c targets -c exit
```

### Serial Port Not Found

```bash
# Check device exists
ls -l /dev/pico2

# Check udev rules
cat /etc/udev/rules.d/99-pico.rules

# Reload rules
sudo udevadm control --reload-rules
sudo udevadm trigger

# Check which tty device
dmesg | grep tty
```

### FSW Not Booting After Flash

```bash
# Check flash was successful
openocd ... -c 'verify_image zephyr.hex' ...

# Monitor serial during reset
screen /dev/pico2 115200
# Press RESET button on Pico 2

# Check for errors in dmesg
dmesg | tail -20
```

### GDS Can't Connect to UART

```bash
# Check port permissions
ls -l /dev/pico2
# User must be in dialout group
sudo usermod -a -G dialout $USER
# Log out and back in

# Check port is not busy
lsof | grep /dev/pico2

# Test raw connection
screen /dev/pico2 115200
```

### Integration Tests Timeout

- Verify ZMQ sockets exist: `ls -l /tmp/fprime-server-*`
- Check GDS is running: `sudo systemctl status fprime-soak-gds`
- Check GDS logs: `sudo journalctl -u fprime-soak-gds -n 100`
- Verify FSW is sending data: `cat /dev/pico2` (should see telemetry)

---

## Differences from Linux Pi Soak Testing

| Aspect | Linux (Two-Pi) | Pico 2 |
|--------|----------------|--------|
| FSW Platform | Native Linux ARM64 | Zephyr RTOS |
| Connection | Ethernet (TCP/IP) | UART (USB Serial) |
| FSW Service | systemd on remote Pi | Bare metal on Pico 2 |
| Deployment | SSH + scp | OpenOCD flash via SWD |
| GDS Communication | TCP socket | UART |
| Power Control | Optional (network switch) | USB power or GPIO |
| Watchdog | Software (systemd) | Hardware (Pico 2 built-in) |

---

## Advanced: Automated Power Cycling

For true soak testing, add power control to reset the Pico 2:

### Option A: USB Power Control (Simple)

Use a USB hub with per-port power control (e.g., uhubctl):

```bash
# Install uhubctl
sudo apt-get install uhubctl

# Power cycle Pico 2
uhubctl -l 1-1 -p 2 -a off
sleep 2
uhubctl -l 1-1 -p 2 -a on
```

### Option B: GPIO Power Control (Advanced)

Connect Pico 2 VBUS through a MOSFET controlled by Pi GPIO:

```bash
# Control via GPIO (example: GPIO 17)
echo 17 > /sys/class/gpio/export
echo out > /sys/class/gpio/gpio17/direction
echo 0 > /sys/class/gpio/gpio17/value  # Power off
sleep 2
echo 1 > /sys/class/gpio/gpio17/value  # Power on
```

---

## Summary

To set up Pico 2 soak testing:

1. **Hardware**: Pi + Pico 2 + Debug Probe + USB cables
2. **Software**: OpenOCD, udev rules, fprime-gds
3. **Workflows**: Build → Flash → Deploy GDS → Soak Test
4. **Key Difference**: UART communication instead of TCP/IP
5. **Flashing**: OpenOCD via SWD instead of scp

The architecture mirrors the two-Pi setup but replaces:
- Remote SSH deployment → OpenOCD flashing
- TCP/IP GDS connection → UART GDS connection
- Systemd FSW service → Bare-metal Zephyr FSW
