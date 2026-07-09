"""test_core_stress.py: generic core-subtopology command stress test.

A deployment-agnostic soak stress test that hammers the commands belonging to
the *core* F Prime subtopologies (CdhCore, and — where present — DataProducts).
It is designed to run against any deployment's persistent soak GDS.

Two portions:

  1. Reachability gate -- each available core command is fired once, then the
     FSW is proven to still service commands. Fail-fast: if the command path is
     fundamentally broken, there's no point running the burst.

  2. Sharp stress burst -- hundreds of non-destructive commands are fired
     back-to-back with no throttle, then the FSW is proven to have survived.
     This is deliberately hard on the comm link (e.g. a 115200-baud UART
     downlink) to surface command-dispatcher queue overflow, buffer leaks,
     crashes, and wedges under load.

What this test asserts (and, deliberately, what it does NOT)
-----------------------------------------------------------
The pass/fail invariant is FSW *survival*, not downlink completeness. A sharp
burst intentionally saturates the telemetry downlink: hundreds of commands fan
out into thousands of events that overflow the bounded ComQueue / event queues,
so many completion events are dropped or delayed. That is expected under flood
-- and on the Zephyr UART downlink, telemetry can lag by seconds to minutes.

An earlier version gated on "how many OpCodeCompleted events came back," which
measured downlink throughput, not FSW health: a perfectly healthy Linux FSW
that executed all 300 commands in ~3 ms still only downlinked ~14 completion
events, and Zephyr's backlogged link failed even a single-command wait. So:

  * completions observed during the burst are logged as INFORMATIONAL only, and
  * the gate is a liveness probe (a fresh NO_OP that eventually completes),
    retried with generous timeouts to tolerate a lossy / backlogged link.

A truly wedged or crashed FSW never services the liveness NO_OP and fails; a
merely lossy downlink does not.

Linux vs. Zephyr support
------------------------
The Zephyr reference only imports CdhCore + ComCcsds, so it lacks the
DataProducts commands the Linux Ref deployment has. Rather than branch on
platform, every candidate command is matched against the *live FSW dictionary*
(pipeline.dictionaries.command_name). Commands the flashed image doesn't have
are skipped and logged. The same test therefore adapts to either platform.

This test deliberately does NOT depend on a deployment int_config.json (the
generic-name -> instance alias table that get_mnemonic() reads). Not every
deployment ships one -- led-blinker, for instance, does not. Instead we read the
fully qualified mnemonics straight from the live dictionary, whose keys look
like "CdhCore.cmdDisp.CMD_NO_OP", and match on the command-name suffix.

Tuning (environment variables, all optional):
  SOAK_STRESS_COMMAND_COUNT     total commands in the burst        (default 300)
  SOAK_STRESS_INTER_CMD_DELAY   seconds between burst sends        (default 0.0 = as fast as possible)
  SOAK_STRESS_LIVENESS_TIMEOUT  seconds per liveness attempt (int) (default 30)
  SOAK_STRESS_LIVENESS_ATTEMPTS liveness retries before failing    (default 3)
  SOAK_STRESS_MIN_COMPLETIONS   optional informational floor; 0 = don't gate (default 0)
"""

import os
import time

# ----------------------------------------------------------------------------
# Candidate core commands, by command-name suffix.
#
# Keys are the bare command names as they appear at the end of a fully qualified
# dictionary mnemonic (e.g. "CMD_NO_OP" in "CdhCore.cmdDisp.CMD_NO_OP"). Values
# are non-destructive args chosen so the command can be fired repeatedly in a
# burst without leaving the FSW in a degraded state.
#
# We match on suffix rather than a hardcoded instance path so this stays generic
# across deployments that name their instances differently.
# ----------------------------------------------------------------------------

# CdhCore -- present on every deployment (Linux and Zephyr).
CDHCORE_COMMANDS = {
    "CMD_NO_OP": [],
    "CMD_NO_OP_STRING": ["soak_stress"],
    "CMD_TEST_CMD_1": [1, 2.0, 3],
    "DUMP_FILTER_STATE": [],
    "VERSION": ["FRAMEWORK"],
}

# DataProducts -- Linux Ref only; absent on the Zephyr reference. Rebuilding the
# catalog is a genuine, non-destructive stressor of the data-product stack.
DATAPRODUCTS_COMMANDS = {
    "BUILD_CATALOG": [],
}

CANDIDATE_COMMANDS = {**CDHCORE_COMMANDS, **DATAPRODUCTS_COMMANDS}

# The command dispatcher emits this event on every completion. Matched by suffix.
OP_CODE_COMPLETED_SUFFIX = "OpCodeCompleted"
NO_OP_SUFFIX = "CMD_NO_OP"


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


def _find_by_suffix(name_dict, suffix):
    """Return the fully qualified key in name_dict ending in ".<suffix>".

    Returns None if no key matches. If several match (multiple instances of the
    same component), the first is returned -- fine for these core singletons.
    """
    for key in name_dict.keys():
        if key == suffix or key.endswith(f".{suffix}"):
            return key
    return None


def _resolve_available_commands(fprime_test_api):
    """Resolve candidate commands against the live FSW dictionary.

    Returns a list of (mnemonic, args) for the commands the flashed image
    actually has, discovered by matching the command-name suffix. Commands
    absent from the dictionary (e.g. DataProducts on Zephyr) are skipped and
    logged -- this is the Linux/Zephyr support hook.
    """
    cmd_dict = fprime_test_api.pipeline.dictionaries.command_name
    available = []
    skipped = []
    for suffix, args in CANDIDATE_COMMANDS.items():
        mnemonic = _find_by_suffix(cmd_dict, suffix)
        if mnemonic is not None:
            available.append((mnemonic, args))
        else:
            skipped.append(suffix)
    if skipped:
        print(f"[stress] Skipping {len(skipped)} command(s) not in this "
              f"deployment's dictionary: {', '.join(skipped)}")
    print(f"[stress] {len(available)} core command(s) available: "
          f"{', '.join(m for m, _ in available)}")
    return available


def _resolve_no_op(fprime_test_api):
    """Return the fully qualified CMD_NO_OP mnemonic, or None if unavailable."""
    cmd_dict = fprime_test_api.pipeline.dictionaries.command_name
    return _find_by_suffix(cmd_dict, NO_OP_SUFFIX)


def _resolve_completed_event(fprime_test_api):
    """Return the fully qualified OpCodeCompleted event mnemonic, or None."""
    event_dict = fprime_test_api.pipeline.dictionaries.event_name
    return _find_by_suffix(event_dict, OP_CODE_COMPLETED_SUFFIX)


def _assert_fsw_alive(fprime_test_api, no_op, completed_event, timeout, attempts):
    """Prove the FSW still services commands by observing a NO_OP completion.

    Sends a fresh NO_OP and awaits at least one OpCodeCompleted event within
    `timeout` seconds, retrying up to `attempts` times. Loss-tolerant on
    purpose: a lossy or backlogged downlink just needs ONE completion to arrive
    across all attempts. A wedged/crashed FSW yields none and fails the assert.
    """
    for attempt in range(1, attempts + 1):
        start = fprime_test_api.get_event_test_history().size()
        fprime_test_api.send_command(no_op)
        found = fprime_test_api.await_event_count(
            1, events=completed_event, start=start, timeout=timeout
        )
        if found:
            print(f"[stress] Liveness OK: NO_OP completion observed on attempt "
                  f"{attempt}/{attempts}")
            return
        print(f"[stress] Liveness attempt {attempt}/{attempts} saw no NO_OP "
              f"completion within {timeout}s; retrying")
    raise AssertionError(
        f"FSW did not service a NO_OP within {attempts} attempts of {timeout}s "
        f"each. FSW appears wedged or crashed (this is NOT a mere downlink "
        f"loss -- not even one completion arrived across all attempts)."
    )


def test_core_commands_reachable(fprime_test_api):
    """Fire each available core command once, then confirm the FSW is alive.

    Fail-fast gate. Sending every core command exercises its uplink, decode, and
    dispatch path; the subsequent liveness probe confirms the FSW processed
    through them without wedging (e.g. a bad-arg command that FW_ASSERTs on board
    would crash the FSW and fail the liveness check). We do NOT assert per-command
    completion events, because downlink loss on a lossy link (Zephyr UART) would
    make that flaky without indicating any real FSW fault.
    """
    available = _resolve_available_commands(fprime_test_api)
    assert available, "No core commands found in the FSW dictionary"

    no_op = _resolve_no_op(fprime_test_api)
    assert no_op is not None, "CMD_NO_OP not available for the liveness check"
    completed_event = _resolve_completed_event(fprime_test_api)
    assert completed_event is not None, (
        f"{OP_CODE_COMPLETED_SUFFIX} event not found in dictionary"
    )

    timeout = _int_env("SOAK_STRESS_LIVENESS_TIMEOUT", 30)
    attempts = _int_env("SOAK_STRESS_LIVENESS_ATTEMPTS", 3)

    for mnemonic, args in available:
        fprime_test_api.send_command(mnemonic, args)

    _assert_fsw_alive(fprime_test_api, no_op, completed_event, timeout, attempts)


def test_core_commands_stress_burst(fprime_test_api):
    """Fire hundreds of core commands back-to-back, then prove FSW survival.

    The burst is intentionally un-throttled to stress the command/telemetry
    path. The gate is survival (a post-burst NO_OP eventually completes), NOT
    downlink completeness -- see the module docstring. The count of completion
    events observed during the burst is logged as an informational
    downlink-throughput datapoint and only gates when SOAK_STRESS_MIN_COMPLETIONS
    is explicitly set > 0.
    """
    available = _resolve_available_commands(fprime_test_api)
    assert available, "No core commands found in the FSW dictionary"

    count = _int_env("SOAK_STRESS_COMMAND_COUNT", 300)
    inter_delay = _float_env("SOAK_STRESS_INTER_CMD_DELAY", 0.0)
    # Timeouts are ints: the test API forwards them to signal.alarm(), which
    # rejects floats with "'float' object cannot be interpreted as an integer".
    liveness_timeout = _int_env("SOAK_STRESS_LIVENESS_TIMEOUT", 30)
    liveness_attempts = _int_env("SOAK_STRESS_LIVENESS_ATTEMPTS", 3)
    # Optional informational floor. 0 (default) means "do not gate on it".
    min_completions = _int_env("SOAK_STRESS_MIN_COMPLETIONS", 0)

    completed_event = _resolve_completed_event(fprime_test_api)
    assert completed_event is not None, (
        f"{OP_CODE_COMPLETED_SUFFIX} event not found in dictionary"
    )
    no_op = _resolve_no_op(fprime_test_api)
    assert no_op is not None, "CMD_NO_OP not available for the liveness check"

    print(f"[stress] Firing {count} commands (inter-command delay={inter_delay}s); "
          f"gate = post-burst survival")

    # Clear histories and record the event history start index so the completion
    # tally counts only events observed after the burst begins.
    fprime_test_api.clear_histories()
    event_start = fprime_test_api.get_event_test_history().size()

    # --- The sharp part: fire everything with no wait-for-completion. ---
    for i in range(count):
        mnemonic, args = available[i % len(available)]
        fprime_test_api.send_command(mnemonic, args)
        if inter_delay > 0:
            time.sleep(inter_delay)

    # Informational: how many completion events actually made it back. On a
    # saturated/lossy downlink this is expected to be << count. We wait briefly
    # for whatever drains within one liveness window, then just report it.
    completions = fprime_test_api.await_event_count(
        max(1, min_completions) if min_completions > 0 else count,
        events=completed_event,
        start=event_start,
        timeout=liveness_timeout,
    )
    num_completed = len(completions)
    print(f"[stress] Observed {num_completed}/{count} OpCodeCompleted events "
          f"downlinked during the burst window (informational; downlink loss "
          f"under flood is expected)")

    # Gate: the FSW must still service commands after the flood.
    _assert_fsw_alive(
        fprime_test_api, no_op, completed_event, liveness_timeout, liveness_attempts
    )

    # Optional secondary gate, only if the operator explicitly opted in.
    if min_completions > 0:
        assert num_completed >= min_completions, (
            f"Only {num_completed} of {count} completions downlinked "
            f"(operator floor SOAK_STRESS_MIN_COMPLETIONS={min_completions})."
        )
