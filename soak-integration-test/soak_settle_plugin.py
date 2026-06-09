"""pytest settle plugin for soak integration tests.

Loaded with ``pytest -p soak_settle_plugin`` (the soak-integration-test action
puts this file on ``PYTHONPATH``) so it never clobbers a deployment's own
``conftest.py`` while still registering its autouse fixture globally.

Why this is needed for soak testing but not for a normal integration-test run:

In a normal run the GDS launches the flight software itself, so the GDS comm
layer and the FSW start together. The GDS consumes the downlink from the moment
the app emits it, there is no backlog, and the ZeroMQ subscription is live before
any command is sent.

In a soak deployment the FSW and a *persistent* GDS run as long-lived systemd
services. When the periodic test job starts, pytest connects a fresh, ephemeral
ZeroMQ subscriber to the running GDS and immediately sends a command. ZeroMQ's
"slow joiner" behavior means that subscription is not live for the first instants
after it connects, so the command's response events can land in that gap and be
dropped - and the first assertion fails with 0 events.

A shell ``sleep`` before pytest cannot fix this because the dead window is inside
the pytest process, right after its fixture connects. This session-scoped,
autouse fixture runs once after the pipeline is connected (the dependency on
``fprime_test_api_session`` guarantees that) and before any test sends a command.
It waits until telemetry is actually being received - which proves the
subscription is live and current - then settles briefly so any backlog has
flushed.

Tunable via environment variables (set by the soak-integration-test action):
  * ``SOAK_GDS_SETTLE_TIMEOUT`` - max seconds to wait for live telemetry (default 30).
  * ``SOAK_GDS_SETTLE_SECONDS`` - extra settle after telemetry is confirmed (default 5).
"""

import os
import time

import pytest


@pytest.fixture(scope="session", autouse=True)
def gds_subscription_settle(fprime_test_api_session):
    api = fprime_test_api_session
    live_timeout = float(os.environ.get("SOAK_GDS_SETTLE_TIMEOUT", "30"))
    extra_settle = float(os.environ.get("SOAK_GDS_SETTLE_SECONDS", "5"))

    # Telemetry is produced every rate-group cycle, so a growing telemetry history
    # confirms the subscription is live and receiving current data.
    deadline = time.time() + live_timeout
    baseline = api.telemetry_history.size()
    while time.time() < deadline and api.telemetry_history.size() == baseline:
        time.sleep(0.5)

    # Brief extra settle so any downlink backlog finishes flushing before the
    # first command is sent.
    time.sleep(extra_settle)
    yield
