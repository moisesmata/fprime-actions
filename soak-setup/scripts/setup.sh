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

rm -rf "${INSTALL_DIR}" # delete anything that was there
mkdir -p "${INSTALL_DIR}"/{bin,dict,gds-logs,ComLoggerFiles,test}

cp artifacts/build-artifacts/*/*/dict/*TopologyDictionary.json "${INSTALL_DIR}/dict/"
cp -r artifacts/int/. "${INSTALL_DIR}/test/"
cp artifacts/build-artifacts/*/*/bin/* "${INSTALL_DIR}/bin/fsw"
chmod +x "${INSTALL_DIR}/bin/fsw"

python3 -m venv --clear "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install -r artifacts/lib/fprime/requirements.txt

# render fprime-gds.yml
sed -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
    "${TEMPLATES}/fprime-gds.yml" > "${INSTALL_DIR}/fprime-gds.yml"

echo "[INFO] Soak setup complete: ${INSTALL_DIR}"
