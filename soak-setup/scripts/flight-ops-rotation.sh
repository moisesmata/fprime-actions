#!/usr/bin/env bash
#
# Flight-ops rotation: "normal flight operations" command traffic for a soak
# deployment. Two modes:
#
#   flight-ops-rotation.sh          nominal-ops loop, forever (ran by systemd service)
#   flight-ops-rotation.sh spike    one short, sharp stress burst (soak-test action)
#
# The loop pauses whenever soak-test holds the cron lock
# ($INSTALL_DIR/.cron-active) so cron-phase load is the only load. 
#
# Command names are the standard Svc/Subtopologies instance names
#
# Environment:
#   DEPLOYMENT_NAME  required; namespaces install dir, ZMQ sockets, service
#   INSTALL_DIR      default $HOME/fprime-soak-$DEPLOYMENT_NAME
#   PLATFORM         "linux" | "linux-remote" | "pico2".  
#                     pico2 skips file downlinks.

set -uo pipefail

INSTALL_DIR="${INSTALL_DIR:-${HOME}/fprime-soak-${DEPLOYMENT_NAME}}"
if [ -z "${PLATFORM:-}" ]; then
  PLATFORM=$(sed -n 's/^Environment=PLATFORM=//p' \
    "/etc/systemd/system/fprime-soak-rotation-${DEPLOYMENT_NAME}.service" 2>/dev/null)
  PLATFORM="${PLATFORM:-linux}"
fi

DICT=$(ls "${INSTALL_DIR}"/dict/*TopologyDictionary.json)
ZMQ_IN="ipc:///tmp/fprime-server-${DEPLOYMENT_NAME}-in"
ZMQ_OUT="ipc:///tmp/fprime-server-${DEPLOYMENT_NAME}-out"
CLI="${INSTALL_DIR}/venv/bin/fprime-cli"
CRON_LOCK="${INSTALL_DIR}/.cron-active"
# CLI session logs go to /tmp, NOT $INSTALL_DIR/gds-logs, which soak_monitor
# parses and truncates; a second writer there would corrupt its accounting.
CLI_LOGS="/tmp/fprime-soak-cli-${DEPLOYMENT_NAME}"

# Standard subtopology command names (see fprime Svc/Subtopologies/).
NO_OP="CdhCore.cmdDisp.CMD_NO_OP"
PRM_SAVE="FileHandling.prmDb.PRM_SAVE_FILE"
SEND_FILE="FileHandling.fileDownlink.SendFile"
# Small file guaranteed to exist on any Linux FSW filesystem; downlink
# destination is namespaced so concurrent deployments never collide.
DOWNLINK_SRC="/etc/hostname"
DOWNLINK_DEST="/tmp/fprime-soak-${DEPLOYMENT_NAME}-downlink"

send() {  # send <full-command-name> [args...]
  local name="$1"
  shift
  local extra=()
  if [ "$#" -gt 0 ]; then
    extra=(--arguments "$@")
  fi
  "${CLI}" command-send "${name}" "${extra[@]}" \
    --dictionary "${DICT}" \
    --zmq-transport "${ZMQ_IN}" "${ZMQ_OUT}" \
    -l "${CLI_LOGS}" --log-directly --disable-data-logging
}

read_channel() {  # wait up to 10 s for one fresh telemetry sample
  "${CLI}" channels -t 10 \
    --dictionary "${DICT}" \
    --zmq-transport "${ZMQ_IN}" "${ZMQ_OUT}" \
    -l "${CLI_LOGS}" --log-directly --disable-data-logging
}

run_rotation() {
  echo "[INFO] Flight-ops rotation loop (deployment=${DEPLOYMENT_NAME}, platform=${PLATFORM})"
  local pass=0
  while true; do
    # Never compete with a cron-driven soak-test window.
    if [ -e "${CRON_LOCK}" ]; then
      sleep 15
      continue
    fi
    pass=$((pass + 1))

    send "${NO_OP}"
    sleep 20
    [ -e "${CRON_LOCK}" ] && continue

    read_channel >/dev/null
    sleep 20
    [ -e "${CRON_LOCK}" ] && continue

    send "${PRM_SAVE}"
    sleep 20
    [ -e "${CRON_LOCK}" ] && continue

    # Occasional small file downlink; pico2 (UART, no FileHandling budget)
    # skips it, and it never runs while the cron lock is held.
    if [[ "${PLATFORM}" != "pico2" ]] && [ $((pass % 5)) -eq 0 ]; then
      send "${SEND_FILE}" "${DOWNLINK_SRC}" "${DOWNLINK_DEST}"
      sleep 20
    fi
  done
}

run_spike() {
  echo "[INFO] Stress spike (deployment=${DEPLOYMENT_NAME}, platform=${PLATFORM})"
  local burst="${SPIKE_BURST_COUNT:-25}"

  # Rapid-fire no-op burst with no pacing: saturate CmdDispatcher.
  echo "[INFO] Spike: ${burst} back-to-back no-ops"
  for _ in $(seq 1 "${burst}"); do
    send "${NO_OP}" >/dev/null
  done

  # Linux only: back-to-back file downlinks to stress BufferManager and disk
  # I/O. (fprime-cli has no uplink subcommand, so round-trips are downlinks.)
  if [[ "${PLATFORM}" != "pico2" ]]; then
    echo "[INFO] Spike: file downlink burst"
    send "${SEND_FILE}" "${DOWNLINK_SRC}" "${DOWNLINK_DEST}-spike-1" >/dev/null
    send "${SEND_FILE}" "${DOWNLINK_SRC}" "${DOWNLINK_DEST}-spike-2" >/dev/null
  fi

  # Both platforms: tight parameter-save loop to stress PrmDb's
  # non-volatile write path.
  echo "[INFO] Spike: parameter save churn"
  for _ in $(seq 1 5); do
    send "${PRM_SAVE}" >/dev/null
  done

  # Recovery proof: a final commanded round trip must still go through.
  echo "[INFO] Spike: recovery no-op"
  send "${NO_OP}"
}

case "${1:-loop}" in
  spike) run_spike ;;
  loop)  run_rotation ;;
  *)     echo "usage: $0 [spike]" >&2; exit 2 ;;
esac
