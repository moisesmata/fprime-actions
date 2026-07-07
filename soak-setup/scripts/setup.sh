#!/usr/bin/env bash
#
# Soak setup, start to finish: stage a built F´ deployment into
# $HOME/fprime-soak-<deployment>, start the flight software, then leave two
# persistent systemd services running on the runner:
#
#   fprime-soak-gds-<deployment>       the GDS, connected to the FSW
#   fprime-soak-rotation-<deployment>  nominal flight-ops command traffic
#
# Everything is namespaced by DEPLOYMENT_NAME (install dir, services, ZMQ
# sockets) so multiple deployments soak side by side on one runner.
#
# Platform conventions (fixed, not configurable):
#   linux        : FSW runs locally on the runner as a systemd service,
#                  TCP 127.0.0.1:50000. The calling workflow downloads the
#                  soak artifact to ./artifacts/.
#   linux-remote : FSW runs on a remote Pi. SSH user 'fprime' (passwordless
#                  sudo for systemctl), TCP port 50000. The calling workflow
#                  downloads the soak artifact to ./artifacts/.
#   pico2        : fprime-ci already built and flashed the board. Serial
#                  /dev/pico2 @ 115200; archive extracted to
#                  ./build-artifacts/, target repo checked out at ./ .
#
# Environment (from action.yml):
#   DEPLOYMENT_NAME : unique deployment name
#   PLATFORM        : "linux" | "linux-remote" | "pico2"
#   FSW_IP          : remote FSW Pi IP (linux-remote only)
#   ACTION_PATH     : path to this action (provides scripts/)

set -euo pipefail

INSTALL_DIR="${HOME}/fprime-soak-${DEPLOYMENT_NAME}"
GDS_SERVICE="fprime-soak-gds-${DEPLOYMENT_NAME}"
FSW_SERVICE="fprime-soak-fsw-${DEPLOYMENT_NAME}"
ROTATION_SERVICE="fprime-soak-rotation-${DEPLOYMENT_NAME}"
ZMQ="--zmq-transport ipc:///tmp/fprime-server-${DEPLOYMENT_NAME}-in ipc:///tmp/fprime-server-${DEPLOYMENT_NAME}-out"

# Install a local systemd unit (text on stdin), (re)start it, verify it's up.
install_unit() {
  local name="$1"
  sudo tee "/etc/systemd/system/${name}.service" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable "${name}"
  sudo systemctl restart "${name}"
  if ! sudo systemctl is-active --quiet "${name}"; then
    echo "::error::${name} failed to start"
    sudo systemctl status "${name}" --no-pager -l || true
    sudo journalctl -u "${name}" --no-pager -n 40 || true
    exit 1
  fi
  echo "[INFO] ${name} is active"
}

# ---- Fresh install area -----------------------------------------------------
# Stop this deployment's old services before wiping the tree they run from.
# The FSW service only exists locally on the linux platform; harmless elsewhere.
sudo systemctl disable --now "${ROTATION_SERVICE}" "${GDS_SERVICE}" "${FSW_SERVICE}" 2>/dev/null || true

rm -rf "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"/{bin,dict,gds-logs,ComLoggerFiles,test}
echo "# SOAK STARTED $(date +%Y-%m-%dT%H:%M:%S)" > "${INSTALL_DIR}/soak.log"

# ---- Stage artifacts --------------------------------------------------------
echo "[INFO] Staging ${PLATFORM} artifacts"
case "${PLATFORM}" in
  linux|linux-remote)
    cp artifacts/build-artifacts/*/*/dict/*TopologyDictionary.json "${INSTALL_DIR}/dict/"
    cp -r artifacts/int/. "${INSTALL_DIR}/test/"
    # Deployment-owned GDS config; fprime-gds picks it up from its cwd.
    cp artifacts/fprime-gds.yml "${INSTALL_DIR}/" 2>/dev/null || true
    cp artifacts/build-artifacts/*/*/bin/* "${INSTALL_DIR}/bin/fsw"
    chmod +x "${INSTALL_DIR}/bin/fsw"
    if [[ "${PLATFORM}" == "linux-remote" ]]; then
      GDS_COMM="--communication-selection ip --ip-address ${FSW_IP} --ip-port 50000 --ip-client"
    else
      GDS_COMM=""  # local FSW connects to the GDS's default socket
    fi
    ;;
  pico2)
    cp "$(find ./build-artifacts -name '*TopologyDictionary.json' | head -n 1)" "${INSTALL_DIR}/dict/"
    INT_DIR=$(find . -path ./lib -prune -o -path ./build-artifacts -prune -o -type d -name int -print 2>/dev/null | head -n 1)
    if [ -n "${INT_DIR}" ] && [ -d "${INT_DIR}" ]; then
      cp -r "${INT_DIR}/." "${INSTALL_DIR}/test/"
    fi
    GDS_COMM="--communication-selection uart --uart-device /dev/pico2 --uart-baud 115200 --uart-skip-port-check"
    ;;
  *)
    echo "::error::Unknown platform: ${PLATFORM}"
    exit 1
    ;;
esac
DICT=$(ls "${INSTALL_DIR}/dict/"*TopologyDictionary.json)

# ---- Soak venv + flight-ops rotation script ---------------------------------
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install fprime-gds
cp "${ACTION_PATH}/scripts/flight-ops-rotation.sh" "${INSTALL_DIR}/"
chmod +x "${INSTALL_DIR}/flight-ops-rotation.sh"

# ---- Start flight software --------------------------------------------------
if [[ "${PLATFORM}" == "linux" ]]; then
  install_unit "${FSW_SERVICE}" <<EOF
[Unit]
Description=F' soak flight software (${DEPLOYMENT_NAME})
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/bin/fsw -a 127.0.0.1 -p 50000
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
elif [[ "${PLATFORM}" == "linux-remote" ]]; then
  FSW_HOST="fprime@${FSW_IP}"
  REMOTE_DIR="/home/fprime/fprime-soak-${DEPLOYMENT_NAME}"
  UNIT_TMP="/tmp/${FSW_SERVICE}.service"
  cat > "${UNIT_TMP}" <<EOF
[Unit]
Description=F' soak flight software (${DEPLOYMENT_NAME})
After=network.target

[Service]
Type=simple
User=fprime
WorkingDirectory=${REMOTE_DIR}
ExecStart=${REMOTE_DIR}/bin/fsw -a 0.0.0.0 -p 50000
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  ssh "${FSW_HOST}" "sudo systemctl disable --now ${FSW_SERVICE} 2>/dev/null || true \
    && sudo rm -rf ${REMOTE_DIR} && mkdir -p ${REMOTE_DIR}/bin"
  scp "${INSTALL_DIR}/bin/fsw" "${FSW_HOST}:${REMOTE_DIR}/bin/fsw" >/dev/null
  scp "${UNIT_TMP}" "${FSW_HOST}:${UNIT_TMP}" >/dev/null
  if ! ssh "${FSW_HOST}" "chmod +x ${REMOTE_DIR}/bin/fsw \
      && sudo mv ${UNIT_TMP} /etc/systemd/system/${FSW_SERVICE}.service \
      && sudo systemctl daemon-reload \
      && sudo systemctl enable ${FSW_SERVICE} \
      && sudo systemctl restart ${FSW_SERVICE} \
      && sudo systemctl is-active --quiet ${FSW_SERVICE}"; then
    echo "::error::${FSW_SERVICE} failed to start on ${FSW_HOST}"
    ssh "${FSW_HOST}" "sudo journalctl -u ${FSW_SERVICE} --no-pager -n 40" || true
    exit 1
  fi
  echo "[INFO] ${FSW_SERVICE} is active on ${FSW_HOST}"
else
  # pico2: fprime-ci already flashed the board; wait for the serial device.
  for i in $(seq 1 10); do
    if [ -e /dev/pico2 ]; then
      break
    fi
    echo "[INFO] Waiting for /dev/pico2 (${i}/10)..."
    sleep 1
  done
  if [ ! -e /dev/pico2 ]; then
    echo "::warning::Serial device /dev/pico2 not found"
  fi
fi

# ---- Persistent GDS service ---------------------------------------------------
install_unit "${GDS_SERVICE}" <<EOF
[Unit]
Description=F' soak GDS (${DEPLOYMENT_NAME})
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/fprime-gds --no-app --logs ${INSTALL_DIR}/gds-logs --dictionary ${DICT} ${GDS_COMM} ${ZMQ}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
# dialout is needed by the pico2 platform for /dev/tty* access; harmless elsewhere.
SupplementaryGroups=dialout

[Install]
WantedBy=multi-user.target
EOF

# ---- Flight-ops rotation service ----------------------------------------------
# Nominal command traffic between cron-driven soak-test runs; pauses whenever
# soak-test holds ${INSTALL_DIR}/.cron-active.
install_unit "${ROTATION_SERVICE}" <<EOF
[Unit]
Description=F' soak flight-ops rotation (${DEPLOYMENT_NAME})
After=${GDS_SERVICE}.service

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${INSTALL_DIR}
Environment=DEPLOYMENT_NAME=${DEPLOYMENT_NAME}
Environment=INSTALL_DIR=${INSTALL_DIR}
Environment=PLATFORM=${PLATFORM}
ExecStart=${INSTALL_DIR}/flight-ops-rotation.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "[INFO] Soak setup complete: ${INSTALL_DIR}"
