#!/usr/bin/env bash
#
# Install a built F' deployment as persistent FSW + GDS systemd services.
# Sudo is required only for /etc/systemd/system writes and systemctl/journalctl;
# the install tree itself is owned by the runner user.
set -euo pipefail

TEMPLATES_DIR="${ACTION_PATH}/templates"
INSTALL_DIR="${INSTALL_DIR:-${HOME}/fprime-soak}"
SERVICE_USER="${SERVICE_USER_INPUT:-$(whoami)}"
FSW_SERVICE="${SERVICE_PREFIX}-fsw"
GDS_SERVICE="${SERVICE_PREFIX}-gds"

# --- Resolve build artifacts (globs allowed) --------------------------------
BINARY_RESOLVED=$(ls -1 ${BINARY_GLOB} 2>/dev/null | head -n1 || true)
[ -n "${BINARY_RESOLVED}" ] || { echo "::error::No flight-software binary matched '${BINARY_GLOB}'"; exit 1; }
DICT_RESOLVED=$(ls -1 ${DICTIONARY_GLOB} 2>/dev/null | head -n1 || true)
[ -n "${DICT_RESOLVED}" ] || { echo "::error::No dictionary matched '${DICTIONARY_GLOB}'"; exit 1; }
DICT_SRC_DIR=$(dirname "${DICT_RESOLVED}")
DICT_BASENAME=$(basename "${DICT_RESOLVED}")
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

if [ -n "${TEST_SOURCE}" ] && [ -d "${TEST_SOURCE}" ]; then
  echo "[INFO] Installing integration tests from ${TEST_SOURCE}"
  rm -rf "${INSTALL_DIR}/test"
  mkdir -p "${INSTALL_DIR}/test"
  cp -r "${TEST_SOURCE}/." "${INSTALL_DIR}/test/"
elif [ -n "${TEST_SOURCE}" ]; then
  echo "::warning::test-source '${TEST_SOURCE}' not found; no integration tests installed"
fi

COM_LOGS_PATH=""
if [ -n "${COM_LOGGER_SUBDIR}" ]; then
  COM_LOGS_PATH="${INSTALL_DIR}/${COM_LOGGER_SUBDIR}"
  echo "[INFO] Preparing ComLogger directory ${COM_LOGS_PATH}"
  rm -rf "${COM_LOGS_PATH}"
  mkdir -p "${COM_LOGS_PATH}"
fi

# --- Virtualenv --------------------------------------------------------------
echo "[INFO] Building virtualenv at ${INSTALL_DIR}/venv"
rm -rf "${INSTALL_DIR}/venv"
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install -U pip wheel setuptools
"${INSTALL_DIR}/venv/bin/pip" install -r "${REQUIREMENTS}"
if [ -n "${EXTRA_PIP}" ]; then
  "${INSTALL_DIR}/venv/bin/pip" install ${EXTRA_PIP}
fi

# Only chown when the service runs as a different user than the deployer.
if [ "${SERVICE_USER}" != "$(whoami)" ]; then
  sudo chown -R "${SERVICE_USER}:" "${INSTALL_DIR}"
fi

# --- Render unit files + GDS config -----------------------------------------
# fprime-gds reads command-line-options from fprime-gds.yml in its working
# directory, so the GDS unit's ExecStart can stay a single command.
if [ -n "${FRAMING_SELECTION}" ]; then
  FRAMING_SELECTION_LINE="  framing-selection: ${FRAMING_SELECTION}"
else
  FRAMING_SELECTION_LINE=""
fi

render() {
  sed \
    -e "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
    -e "s#__SERVICE_USER__#${SERVICE_USER}#g" \
    -e "s#__SERVICE_PREFIX__#${SERVICE_PREFIX}#g" \
    -e "s#__BINARY_NAME__#${BINARY_NAME}#g" \
    -e "s#__FSW_BIND_ADDRESS__#${FSW_BIND_ADDRESS}#g" \
    -e "s#__FSW_PORT__#${FSW_PORT}#g" \
    -e "s#__FSW_EXTRA_ARGS__#${FSW_EXTRA_ARGS}#g" \
    -e "s#__FSW_SERVICE_NAME__#${FSW_SERVICE}#g" \
    -e "s#__GDS_CONNECT_ADDRESS__#${GDS_CONNECT_ADDRESS}#g" \
    -e "s#__FRAMING_SELECTION_LINE__#${FRAMING_SELECTION_LINE}#g" \
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

{
  echo "install-dir=${INSTALL_DIR}"
  echo "venv=${INSTALL_DIR}/venv"
  echo "dictionary=${INSTALL_DIR}/dict/${DICT_BASENAME}"
  echo "com-logs=${COM_LOGS_PATH}"
  echo "fsw-service=${FSW_SERVICE}"
  echo "gds-service=${GDS_SERVICE}"
} >> "${GITHUB_OUTPUT}"

echo "[INFO] Soak deploy complete: ${FSW_SERVICE} + persistent ${GDS_SERVICE}"
