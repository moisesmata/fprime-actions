#!/usr/bin/env bash
#
# Soak setup: copy the build artifacts into $HOME/fprime-soak and build the
# soak virtualenv.
#
# Expected artifact layout (staged by the calling workflow into ./artifacts/):
#   artifacts/build-artifacts/<arch>/<deployment>/bin/<binary>
#   artifacts/build-artifacts/<arch>/<deployment>/dict/*TopologyDictionary.json
#   artifacts/lib/fprime/requirements.txt
#   artifacts/int/                

set -euo pipefail

INSTALL_DIR="${HOME}/fprime-soak"
TEMPLATES="${ACTION_PATH}/templates"

render() {
  sed -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
      -e "s#__SERVICE_USER__#$(whoami)#g" \
      -e "s#__GDS_ARGS__#${GDS_ARGS}#g" "$1"
}

rm -rf "${INSTALL_DIR}" # delete anything that was there
mkdir -p "${INSTALL_DIR}"/{bin,dict,gds-logs,ComLoggerFiles,test}

cp artifacts/build-artifacts/*/*/dict/*TopologyDictionary.json "${INSTALL_DIR}/dict/"
cp -r artifacts/int/. "${INSTALL_DIR}/test/"
cp artifacts/build-artifacts/*/*/bin/* "${INSTALL_DIR}/bin/fsw"
chmod +x "${INSTALL_DIR}/bin/fsw"

python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install fprime-gds 

# render fprime-gds.yml
sed -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
    "${TEMPLATES}/fprime-gds.yml" > "${INSTALL_DIR}/fprime-gds.yml"

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