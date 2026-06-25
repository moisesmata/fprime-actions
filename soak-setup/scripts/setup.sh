#!/usr/bin/env bash
#
# Soak setup: copy the build artifacts into $HOME/fprime-soak and build the
# soak virtualenv.
#
# Expected artifact layout (staged by the calling workflow into ./artifacts/):
#   artifacts/build-artifacts/<arch>/<deployment>/bin/<binary>
#   artifacts/build-artifacts/<arch>/<deployment>/dict/*TopologyDictionary.json
#   artifacts/int/
#   artifacts/fprime-gds.yml        

set -euo pipefail

INSTALL_DIR="${HOME}/fprime-soak"
TEMPLATES="${ACTION_PATH}/templates"

render() {
  sed -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
      -e "s#__SERVICE_USER__#$(whoami)#g" \
      -e "s#__GDS_ARGS__#${GDS_ARGS}#g" \
      -e "s#__DICT_PATH__#${DICT_PATH}#g" "$1"
}

# Delete anything that was there
rm -rf "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"/{bin,dict,gds-logs,ComLoggerFiles,test}

# Soak database: append-only record of trend telemetry, FSW events, and
# monitor-derived alerts across the full soak lifetime. Lets us delete the events.log and channels.log
echo "# SOAK STARTED $(date +%Y-%m-%dT%H:%M:%S)" > "${INSTALL_DIR}/soak-database.log"

cp artifacts/build-artifacts/*/*/dict/*TopologyDictionary.json "${INSTALL_DIR}/dict/"
cp artifacts/fprime-gds.yml "${INSTALL_DIR}/fprime-gds.yml"
cp -r artifacts/int/. "${INSTALL_DIR}/test/"
cp artifacts/build-artifacts/*/*/bin/* "${INSTALL_DIR}/bin/fsw"
chmod +x "${INSTALL_DIR}/bin/fsw"

DICT_PATH=$(ls "${INSTALL_DIR}/dict/"*TopologyDictionary.json | head -n 1)

python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install fprime-gds

echo "[INFO] Soak Setup Complete: ${INSTALL_DIR}"

# Setup and start GDS service
sudo systemctl disable --now "fprime-soak-gds" 2>/dev/null || true
render "${TEMPLATES}/gds.service.template" \
| sudo tee "/etc/systemd/system/fprime-soak-gds.service" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now fprime-soak-gds

# Verify GDS service started
sudo systemctl is-active --quiet "fprime-soak-gds" && { echo "[INFO] fprime-soak-gds is active"; exit 0; }
echo "::error::fprime-soak-gds failed to start"
sudo systemctl status "fprime-soak-gds" --no-pager -l || true
sudo journalctl -u "fprime-soak-gds" --no-pager -n 40 || true
exit 1
