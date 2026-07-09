"""test_core_stress.py: generic core-subtopology command stress test.

A deployment-agnostic soak stress test that hammers the commands belonging to
the *core* F Prime subtopologies (CdhCore, and — where present — DataProducts).
It is designed to run against any deployment's persistent soak GDS.

Two portions:

  1. Reachability gate -- each available core command is fired once and its
     completion is required. Fail-fast: if a core command can't round-trip even
     once, there's no point running the burst.

  2. Sharp stress burst -- hundreds of non-destructive commands are fired
     back-to-back with no throttle, and EVERY command's completion event must be
     received. This is deliberately hard on the comm link (e.g. a 115200-baud
     UART downlink) and on the command dispatcher / ComQueue.

What this test asserts
----------------------
The pass/fail invariant is that we receive one OpCodeCompleted event for every
command sent -- i.e. zero dropped completions. The FSW is run at raised CPU
priority so the ComQueue does not overflow under the burst, so full downlink of
every completion is the expected, required behavior. Any shortfall means
commands were dropped, completions were lost on the link, or the ComQueue
overflowed -- all of which are real faults this soak test exists to catch.

Because a lossy or slow link (notably the Zephyr UART downlink) can deliver
completions well after the last command is sent, we await the full count with a
generous, configurable timeout rather than a fixed short window. The timeout
bounds how long we wait for the downlink to drain, not what we accept.

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
  SOAK_STRESS_COMMAND_COUNT   total commands in the burst              (default 300)
  SOAK_STRESS_INTER_CMD_DELAY seconds between burst sends              (default 0.0 = as fast as possible)
  SOAK_STRESS_DRAIN_TIMEOUT   seconds to await all completions (int)   (default 120)
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


def _resolve_completed_event(fprime_test_api):
    """Return the fully qualified OpCodeCompleted event mnemonic, or None."""
    event_dict = fprime_test_api.pipeline.dictionaries.event_name
    return _find_by_suffix(event_dict, OP_CODE_COMPLETED_SUFFIX)


def _fire_and_assert_all_completed(
    fprime_test_api, sends, completed_event, timeout, inter_delay=0.0
):
    """Fire every (mnemonic, args) in `sends`, then require ALL completions.

    Sends the whole batch (optionally throttled by inter_delay seconds) with no
    wait-for-completion (the sharp part), then awaits exactly len(sends)
    OpCodeCompleted events. await_event_count returns whatever it found when the
    count is reached or the timeout elapses, so we assert on the returned length:
    full count == every command completed and every completion was downlinked
    (zero drops).
    """
    expected = len(sends)

    # Clear histories and record the event start index so we count only the
    # completions triggered by this batch.
    fprime_test_api.clear_histories()
    event_start = fprime_test_api.get_event_test_history().size()

    for mnemonic, args in sends:
        fprime_test_api.send_command(mnemonic, args)
        if inter_delay > 0:
            time.sleep(inter_delay)

    completions = fprime_test_api.await_event_count(
        expected,
        events=completed_event,
        start=event_start,
        timeout=timeout,
    )
    num_completed = len(completions)
    print(f"[stress] Received {num_completed}/{expected} OpCodeCompleted events")

    assert num_completed >= expected, (
        f"Only {num_completed} of {expected} commands completed within "
        f"{timeout}s. Missing {expected - num_completed} completion event(s): "
        f"commands were dropped, completions were lost on the downlink, or the "
        f"ComQueue overflowed."
    )


def test_core_commands_reachable(fprime_test_api):
    """Fire each available core command once and require every completion.

    Fail-fast gate: exercises each core command's uplink, decode, and dispatch
    path, and requires all of their completion events back before the burst runs.
    """
    available = _resolve_available_commands(fprime_test_api)
    assert available, "No core commands found in the FSW dictionary"

    completed_event = _resolve_completed_event(fprime_test_api)
    assert completed_event is not None, (
        f"{OP_CODE_COMPLETED_SUFFIX} event not found in dictionary"
    )

    # Timeout is an int: the test API forwards it to signal.alarm(), which
    # rejects floats with "'float' object cannot be interpreted as an integer".
    timeout = _int_env("SOAK_STRESS_DRAIN_TIMEOUT", 120)

    _fire_and_assert_all_completed(
        fprime_test_api, available, completed_event, timeout
    )


def test_core_commands_stress_burst(fprime_test_api):
    """Fire hundreds of core commands back-to-back; require EVERY completion.

    The burst is intentionally un-throttled to stress the command/telemetry
    path. With the FSW at raised CPU priority the ComQueue should not overflow,
    so all `count` completion events are expected to be downlinked. We await the
    full count with a generous timeout (to let a slow link drain) and assert we
    received every one.
    """
    available = _resolve_available_commands(fprime_test_api)
    assert available, "No core commands found in the FSW dictionary"

    count = _int_env("SOAK_STRESS_COMMAND_COUNT", 300)
    inter_delay = _float_env("SOAK_STRESS_INTER_CMD_DELAY", 0.0)
    timeout = _int_env("SOAK_STRESS_DRAIN_TIMEOUT", 120)

    completed_event = _resolve_completed_event(fprime_test_api)
    assert completed_event is not None, (
        f"{OP_CODE_COMPLETED_SUFFIX} event not found in dictionary"
    )

    print(f"[stress] Firing {count} commands (inter-command delay={inter_delay}s); "
          f"requiring all {count} completions within {timeout}s")

    # Build the burst list, cycling through the available commands.
    sends = [available[i % len(available)] for i in range(count)]

    _fire_and_assert_all_completed(
        fprime_test_api, sends, completed_event, timeout, inter_delay=inter_delay
    )
