#!/usr/bin/env bash
#
# Install a built F' deployment as persistent FSW + GDS systemd services.
#
# Soak conventions (no inputs needed; the calling workflow stages an artifact
# matching this layout into ./artifacts/):
#   artifacts/build-artifacts/<arch>/<deployment>/bin/<binary>
#   artifacts/build-artifacts/<arch>/<deployment>/dict/*TopologyDictionary.json
#   artifacts/lib/fprime/requirements.txt    # standard fprime overlay path
#   artifacts/int/                           # integration tests, staged at the workflow level
#
# Sudo is required only for systemctl/journalctl and writing into
# /etc/systemd/system/; the install tree is owned by the runner user.
set -euo pipefail

INSTALL_DIR="${HOME}/fprime-soak"
SERVICE_USER="$(whoami)"
SERVICE_PREFIX="fprime-soak"
FSW_SERVICE="${SERVICE_PREFIX}-fsw"
GDS_SERVICE="${SERVICE_PREFIX}-gds"
BINARY_NAME="fsw"
COM_LOGGER_SUBDIR="ComLoggerFiles"

ARTIFACTS_DIR="artifacts"
TEMPLATES_DIR="${ACTION_PATH}/templates"

# --- Resolve build artifacts ------------------------------------------------
BINARY_RESOLVED=$(ls -1 ${ARTIFACTS_DIR}/build-artifacts/*/*/bin/* 2>/dev/null | head -n1 || true)
[ -n "${BINARY_RESOLVED}" ] || { echo "::error::No flight-software binary under ${ARTIFACTS_DIR}/build-artifacts/*/*/bin/"; exit 1; }
DICT_RESOLVED=$(ls -1 ${ARTIFACTS_DIR}/build-artifacts/*/*/dict/*TopologyDictionary.json 2>/dev/null | head -n1 || true)
[ -n "${DICT_RESOLVED}" ] || { echo "::error::No *TopologyDictionary.json under ${ARTIFACTS_DIR}/build-artifacts/*/*/dict/"; exit 1; }
DICT_SRC_DIR=$(dirname "${DICT_RESOLVED}")
DICT_BASENAME=$(basename "${DICT_RESOLVED}")
REQUIREMENTS="${ARTIFACTS_DIR}/lib/fprime/requirements.txt"
TEST_SOURCE="${ARTIFACTS_DIR}/int"
echo "[INFO] Binary:     ${BINARY_RESOLVED}"
echo "[INFO] Dictionary: ${DICT_RESOLVED}"

# --- Stop any previous soak services so we get a clean slot -----------------
for svc in "${FSW_SERVICE}" "${GDS_SERVICE}"; do
  sudo systemctl stop "${svc}" 2>/dev/null || true
  sudo systemctl disable "${svc}" 2>/dev/null || true
done

# --- Install tree (user-owned; no sudo) -------------------------------------
echo "[INFO] Installing into ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}/bin" "${INSTALL_DIR}/dict" "${INSTALL_DIR}/gds-logs"
cp "${BINARY_RESOLVED}" "${INSTALL_DIR}/bin/${BINARY_NAME}"
chmod +x "${INSTALL_DIR}/bin/${BINARY_NAME}"
cp -r "${DICT_SRC_DIR}/." "${INSTALL_DIR}/dict/"

if [ -d "${TEST_SOURCE}" ]; then
  echo "[INFO] Installing integration tests from ${TEST_SOURCE}"
  rm -rf "${INSTALL_DIR}/test"
  mkdir -p "${INSTALL_DIR}/test"
  cp -r "${TEST_SOURCE}/." "${INSTALL_DIR}/test/"
else
  echo "::warning::no integration tests staged at ${TEST_SOURCE}"
fi

COM_LOGS_PATH="${INSTALL_DIR}/${COM_LOGGER_SUBDIR}"
echo "[INFO] Preparing ComLogger directory ${COM_LOGS_PATH}"
rm -rf "${COM_LOGS_PATH}"
mkdir -p "${COM_LOGS_PATH}"

# --- Virtualenv -------------------------------------------------------------
# psutil is needed by the soak-monitor python script; ship it here so the
# monitor action stays input-free and uses this venv.
echo "[INFO] Building virtualenv at ${INSTALL_DIR}/venv"
rm -rf "${INSTALL_DIR}/venv"
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install -U pip wheel setuptools
"${INSTALL_DIR}/venv/bin/pip" install -r "${REQUIREMENTS}" psutil

# --- Render unit files + GDS config -----------------------------------------
# fprime-gds reads command-line-options from fprime-gds.yml in its working
# directory, so the GDS unit's ExecStart can stay a single command.
render() {
  sed \
    -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
    -e "s#__SERVICE_USER__#${SERVICE_USER}#g" \
    -e "s#__SERVICE_PREFIX__#${SERVICE_PREFIX}#g" \
    -e "s#__BINARY_NAME__#${BINARY_NAME}#g" \
    -e "s#__FSW_SERVICE_NAME__#${FSW_SERVICE}#g" \
    -e "s#__GDS_ARGS__#${GDS_ARGS}#g" \
    "$1"
}

render "${TEMPLATES_DIR}/fprime-gds.yml" > "${INSTALL_DIR}/fprime-gds.yml"
echo "::group::${INSTALL_DIR}/fprime-gds.yml"
cat "${INSTALL_DIR}/fprime-gds.yml"
echo "::endgroup::"

render "${TEMPLATES_DIR}/fsw.service" | sudo tee "/etc/systemd/system/${FSW_SERVICE}.service" > /dev/null
render "${TEMPLATES_DIR}/gds.service" | sudo tee "/etc/systemd/system/${GDS_SERVICE}.service" > /dev/null
echo "::group::${FSW_SERVICE}.service"
cat "/etc/systemd/system/${FSW_SERVICE}.service"
echo "::endgroup::"
echo "::group::${GDS_SERVICE}.service"
cat "/etc/systemd/system/${GDS_SERVICE}.service"
echo "::endgroup::"

# --- Start services ---------------------------------------------------------
sudo systemctl daemon-reload
sudo systemctl enable "${FSW_SERVICE}" "${GDS_SERVICE}"
sudo systemctl restart "${FSW_SERVICE}"
sleep 3
sudo systemctl restart "${GDS_SERVICE}"
sleep 5

for svc in "${FSW_SERVICE}" "${GDS_SERVICE}"; do
  if sudo systemctl is-active --quiet "${svc}"; then
    echo "[INFO] ${svc} is active"
  else
    echo "::error::${svc} failed to start"
    sudo systemctl status "${svc}" --no-pager -l || true
    sudo journalctl -u "${svc}" --no-pager -n 40 || true
    exit 1
  fi
done

echo "[INFO] Soak deploy complete: ${FSW_SERVICE} + persistent ${GDS_SERVICE}"
