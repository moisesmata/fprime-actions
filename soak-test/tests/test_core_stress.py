"""test_core_stress.py: generic core-subtopology command stress test.

A deployment-agnostic soak stress test that hammers the commands belonging to
the *core* F Prime subtopologies (CdhCore, and — where present — DataProducts).
It is designed to run against any deployment's persistent soak GDS.

Two portions:

  1. Reachability gate -- each available core command is fired once and its
     completion is required. Fail-fast: if a core command can't round-trip even
     once, there's no point running the sustained load.

  2. Sustained load, zero loss -- a total of SOAK_STRESS_COMMAND_COUNT commands
     is sent as a sequence of small batches. Each batch is fully drained (all of
     its OpCodeCompleted events received) before the next batch is sent, and
     EVERY command's completion must arrive. Across the whole run this asserts
     zero dropped completions.

What this test asserts, and why it is throttled
-----------------------------------------------
The pass/fail invariant is that we receive one OpCodeCompleted event for every
command sent -- i.e. zero dropped completions -- as a measure of downlink
integrity under load.

Completions ride the telemetry downlink, which is rate-limited and finite. In
the CCSDS Com stack, command completions become events that queue in ComQueue's
bounded, drop-on-full events sub-queue and drain only when comQueue.run fires
(e.g. at 1 Hz) under comStatus flow control. If commands are fired faster than
that queue drains, it overflows and completions are dropped at enqueue -- a loss
no receive-side timeout can recover.

So we do NOT fire an instantaneous burst. We send in small batches and fully
drain each batch (await all its completions) before sending the next. This
closed-loop pacing self-adapts to the actual link speed -- fast on a Linux TCP
link, slow on a 115200-baud Zephyr UART -- and keeps at most one batch's worth
of events in flight, so the queue never overflows on a healthy system. A real
fault (dropped command, lost completion, queue overflow, wedge) still fails,
because that batch will not fully drain within the timeout.

Tune SOAK_STRESS_BATCH_SIZE down if a deployment's events queue is shallow, or
up for more in-flight pressure.

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
  SOAK_STRESS_COMMAND_COUNT   total commands to send                   (default 500)
  SOAK_STRESS_BATCH_SIZE      commands per drain-synced batch          (default 20)
  SOAK_STRESS_DRAIN_TIMEOUT   seconds to await one batch's completions (default 60)
"""

import os

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


def _drain_batch(fprime_test_api, sends, completed_event, timeout, label):
    """Fire one batch, then require ALL of its completions before returning.

    Sends every (mnemonic, args) in `sends` with no per-command wait, then awaits
    exactly len(sends) OpCodeCompleted events. await_event_count returns whatever
    it found when the count is reached or the timeout elapses, so we assert on the
    returned length. Because the caller only starts the NEXT batch after this one
    fully drains, at most one batch's worth of events is ever in ComQueue at once
    -- this is the throttle that keeps the events sub-queue from overflowing.
    """
    expected = len(sends)

    # Record the event history start index so we count only THIS batch's
    # completions. (History is cleared once, by the caller, before batch 0.)
    event_start = fprime_test_api.get_event_test_history().size()

    for mnemonic, args in sends:
        fprime_test_api.send_command(mnemonic, args)

    completions = fprime_test_api.await_event_count(
        expected,
        events=completed_event,
        start=event_start,
        timeout=timeout,
    )
    num_completed = len(completions)
    print(f"[stress] {label}: received {num_completed}/{expected} completions")

    assert num_completed >= expected, (
        f"{label}: only {num_completed} of {expected} completions received "
        f"within {timeout}s. Missing {expected - num_completed}: a command was "
        f"dropped, a completion was lost on the downlink, or the ComQueue events "
        f"sub-queue overflowed."
    )


def _run_sustained_load(fprime_test_api, sends, completed_event, batch_size, timeout):
    """Send `sends` as drain-synced batches; require every completion overall.

    Splits the command list into batches of `batch_size`, draining each batch
    fully before starting the next. Self-paces to the actual link speed and keeps
    only one batch in flight, so a healthy system never overflows and every
    completion is accounted for.
    """
    fprime_test_api.clear_histories()
    total = len(sends)
    num_batches = (total + batch_size - 1) // batch_size
    for b in range(num_batches):
        batch = sends[b * batch_size:(b + 1) * batch_size]
        _drain_batch(
            fprime_test_api, batch, completed_event, timeout,
            label=f"batch {b + 1}/{num_batches}",
        )


def test_core_commands_reachable(fprime_test_api):
    """Fire each available core command once and require every completion.

    Fail-fast gate: exercises each core command's uplink, decode, and dispatch
    path, and requires all of their completion events back before the sustained
    load runs.
    """
    available = _resolve_available_commands(fprime_test_api)
    assert available, "No core commands found in the FSW dictionary"

    completed_event = _resolve_completed_event(fprime_test_api)
    assert completed_event is not None, (
        f"{OP_CODE_COMPLETED_SUFFIX} event not found in dictionary"
    )

    # Timeout is an int: the test API forwards it to signal.alarm(), which
    # rejects floats with "'float' object cannot be interpreted as an integer".
    timeout = _int_env("SOAK_STRESS_DRAIN_TIMEOUT", 60)

    fprime_test_api.clear_histories()
    _drain_batch(
        fprime_test_api, available, completed_event, timeout, label="reachability"
    )


def test_core_commands_sustained_load(fprime_test_api):
    """Send many commands as drain-synced batches; require EVERY completion.

    Sustained-load, zero-loss soak: SOAK_STRESS_COMMAND_COUNT commands are sent
    in batches of SOAK_STRESS_BATCH_SIZE, each batch fully drained before the next
    so ComQueue never overflows on a healthy system. Asserts that every command's
    completion event is received -- any drop fails the batch it occurred in.
    """
    available = _resolve_available_commands(fprime_test_api)
    assert available, "No core commands found in the FSW dictionary"

    count = _int_env("SOAK_STRESS_COMMAND_COUNT", 500)
    batch_size = max(1, _int_env("SOAK_STRESS_BATCH_SIZE", 20))
    timeout = _int_env("SOAK_STRESS_DRAIN_TIMEOUT", 60)

    completed_event = _resolve_completed_event(fprime_test_api)
    assert completed_event is not None, (
        f"{OP_CODE_COMPLETED_SUFFIX} event not found in dictionary"
    )

    print(f"[stress] Sending {count} commands in batches of {batch_size}, "
          f"draining each fully (<= {timeout}s/batch); requiring every completion")

    # Cycle through the available commands for an even split across command types.
    sends = [available[i % len(available)] for i in range(count)]

    _run_sustained_load(
        fprime_test_api, sends, completed_event, batch_size, timeout
    )
