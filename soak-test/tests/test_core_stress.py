"""test_core_stress.py: generic core-subtopology command stress test.

A deployment-agnostic soak stress test that hammers the commands belonging to
the *core* F Prime subtopologies (CdhCore, and — where present — DataProducts).
It is designed to run against any deployment's persistent soak GDS.

Two portions:

  1. Correctness pass -- each available core command is sent once with
     send_and_assert_command, proving dispatch->complete works and args decode.

  2. Sharp stress burst -- hundreds of non-destructive commands are fired
     back-to-back with no throttle, then the FSW is proven still alive and to
     have processed a floor number of them. This is deliberately hard on the
     comm link (e.g. a 115200-baud UART downlink) to surface dropped bytes,
     command-dispatcher queue overflow, and telemetry back-pressure.

Linux vs. Zephyr support
------------------------
The Zephyr reference only imports CdhCore + ComCcsds, so it lacks the
DataProducts commands the Linux Ref deployment has. Rather than branch on
platform, every candidate command is looked up in the *live FSW dictionary*
(pipeline.dictionaries.command_name). Commands the flashed image doesn't have
are skipped and logged. The same test therefore adapts to either platform.

Tuning (environment variables, all optional):
  SOAK_STRESS_COMMAND_COUNT  total commands in the burst        (default 300)
  SOAK_STRESS_MIN_COMPLETIONS floor of completions to require   (default count//10, min 1)
  SOAK_STRESS_INTER_CMD_DELAY seconds between burst sends       (default 0.0 = as fast as possible)
  SOAK_STRESS_LIVENESS_TIMEOUT seconds to await post-burst NO_OP (default 30)
"""

import os

import pytest

# ----------------------------------------------------------------------------
# Candidate core commands.
#
# Each entry is (generic_type, command_name, args). generic_type is the
# platform-independent name resolved through int_config.json via get_mnemonic;
# args are chosen to be non-destructive so the command can be fired repeatedly
# in a burst without leaving the FSW in a degraded state.
# ----------------------------------------------------------------------------

# CdhCore -- present on every deployment (Linux and Zephyr).
CDHCORE_COMMANDS = [
    ("Svc.CommandDispatcher", "CMD_NO_OP", []),
    ("Svc.CommandDispatcher", "CMD_NO_OP_STRING", ["soak_stress"]),
    ("Svc.CommandDispatcher", "CMD_TEST_CMD_1", [1, 2.0, 3]),
    ("Svc.EventManager", "DUMP_FILTER_STATE", []),
    ("Svc.Version", "VERSION", ["FRAMEWORK"]),
]

# DataProducts -- Linux Ref only; absent on the Zephyr reference. Rebuilding the
# catalog is a genuine, non-destructive stressor of the data-product stack.
DATAPRODUCTS_COMMANDS = [
    ("Svc.DpCatalog", "BUILD_CATALOG", []),
]

CANDIDATE_COMMANDS = CDHCORE_COMMANDS + DATAPRODUCTS_COMMANDS


def _int_env(name, default):
    """Read a non-negative int from the environment, falling back to default."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _resolve_available_commands(fprime_test_api):
    """Resolve candidate commands against the live FSW dictionary.

    Returns a list of (mnemonic, args) for the commands the flashed image
    actually has. Commands absent from the dictionary (e.g. DataProducts on
    Zephyr) are skipped and logged -- this is the Linux/Zephyr support hook.
    """
    cmd_dict = fprime_test_api.pipeline.dictionaries.command_name
    available = []
    skipped = []
    for comp, name, args in CANDIDATE_COMMANDS:
        mnemonic = fprime_test_api.get_mnemonic(comp, name)
        if mnemonic in cmd_dict:
            available.append((mnemonic, args))
        else:
            skipped.append(mnemonic)
    if skipped:
        print(f"[stress] Skipping {len(skipped)} command(s) not in this "
              f"deployment's dictionary: {', '.join(skipped)}")
    print(f"[stress] {len(available)} core command(s) available: "
          f"{', '.join(m for m, _ in available)}")
    return available


def test_core_commands_correctness(fprime_test_api):
    """Send each available core command once, asserting dispatch->complete.

    This is the fail-fast correctness gate: if a core command can't round-trip
    even once, there's no point running the burst.
    """
    available = _resolve_available_commands(fprime_test_api)
    assert available, "No core commands found in the FSW dictionary"

    for mnemonic, args in available:
        fprime_test_api.send_and_assert_command(mnemonic, args, timeout=10)


def test_core_commands_stress_burst(fprime_test_api):
    """Fire hundreds of core commands back-to-back, then prove FSW liveness.

    The burst is intentionally un-throttled to stress the command/telemetry
    path. Because a real link (especially UART) may drop bytes under load, we
    do NOT require every command to complete. We require:
      * the FSW is still alive afterward (a fresh NO_OP completes), and
      * at least SOAK_STRESS_MIN_COMPLETIONS commands completed during the burst.
    """
    available = _resolve_available_commands(fprime_test_api)
    assert available, "No core commands found in the FSW dictionary"

    count = _int_env("SOAK_STRESS_COMMAND_COUNT", 300)
    inter_delay = _float_env("SOAK_STRESS_INTER_CMD_DELAY", 0.0)
    liveness_timeout = _float_env("SOAK_STRESS_LIVENESS_TIMEOUT", 30.0)
    min_completions = _int_env(
        "SOAK_STRESS_MIN_COMPLETIONS", max(1, count // 10)
    )

    # OpCodeCompleted is emitted by the command dispatcher on every completion.
    # Resolve its mnemonic the same way as commands so it matches the dictionary.
    completed_event = fprime_test_api.get_mnemonic(
        "Svc.CommandDispatcher", "OpCodeCompleted"
    )

    print(f"[stress] Firing {count} commands (inter-command delay={inter_delay}s); "
          f"requiring >= {min_completions} completions and post-burst liveness")

    # Clear histories so completion counting starts fresh, and record the event
    # history start index so we count only completions from this burst.
    fprime_test_api.clear_histories()
    event_start = fprime_test_api.get_event_test_history().size()

    # --- The sharp part: fire everything with no wait-for-completion. ---
    for i in range(count):
        mnemonic, args = available[i % len(available)]
        fprime_test_api.send_command(mnemonic, args)
        if inter_delay > 0:
            import time

            time.sleep(inter_delay)

    # Count how many OpCodeCompleted events landed. On a lossy link this is < count;
    # a hung/crashed FSW yields ~0. await_event_count returns whatever it found on
    # timeout, so we assert on the returned length rather than trusting completion.
    completions = fprime_test_api.await_event_count(
        min_completions,
        events=completed_event,
        start=event_start,
        timeout=liveness_timeout,
    )
    num_completed = len(completions)
    print(f"[stress] Observed {num_completed} OpCodeCompleted events during burst "
          f"({num_completed}/{count})")

    # Liveness: a fresh NO_OP must dispatch AND complete after the burst. This is
    # the strict survival gate -- it proves the dispatcher drained and the comm
    # path recovered.
    fprime_test_api.send_and_assert_command(
        fprime_test_api.get_mnemonic("Svc.CommandDispatcher", "CMD_NO_OP"),
        timeout=liveness_timeout,
    )

    assert num_completed >= min_completions, (
        f"Only {num_completed} of {count} stress commands completed "
        f"(floor {min_completions}). FSW may be dropping commands or wedged."
    )
