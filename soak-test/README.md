# nasa/fprime-actions/soak-test

The `soak-test` action runs the periodic half of a soak: analyze data captured
by the persistent GDS service for health/resource/stability problems, fire a
short high-intensity stress spike, then run the deployment's integration tests
against the still-running GDS.

It pairs with [`soak-setup`](../soak-setup/) and assumes its conventions
(`$HOME/fprime-soak-<deployment-name>/{venv,dict,gds-logs,ComLoggerFiles,test}`,
namespaced ZMQ sockets, and the `fprime-soak-{gds,rotation}-<deployment-name>`
systemd services).

What it does, in order:

1. Takes the per-deployment `.cron-active` lock so the deployment's flight-ops
   rotation loop (see soak-setup) pauses for the duration of the run. The lock
   is namespaced by deployment and removed on exit, even on failure.
2. Runs `soak_monitor.py`: decodes GDS text logs (ComLogger `.com` files as
   fallback), raises alerts for FATAL/WARNING events and threshold breaches,
   runs trend analysis over the accumulated history in the append-only
   `soak.log`, then truncates the GDS logs for the next window.
3. Runs `flight-ops-rotation.sh spike`: the stress spike (see below).
4. Runs `pytest` against `$HOME/fprime-soak-<deployment-name>/test/` (the
   deployment's own integration tests) over the namespaced ZMQ transport,
   doubling as the post-spike health check.

Any FATAL event, threshold breach, spike failure, or pytest failure fails the
step - all phases run before the step exits.

## Usage

```yaml
- uses: nasa/fprime-actions/soak-test@devel
  with:
    deployment-name: my-deployment
```

## Telemetry analysis

Threshold checks run on every sample in the current window:

| Channel suffix               | Rule           | Alert                     |
|------------------------------|----------------|---------------------------|
| `CPU`                        | > 85%          | high average CPU usage    |
| `NoBuffs`                    | > 0            | buffer allocation failure |
| `EmptyBuffs`                 | > 0            | empty buffer returned     |
| `RgCycleSlips` / `CycleSlips`| > 0            | rate group cycle slip     |
| `NON_VOLATILE_FREE`          | < 1 GiB        | storage depletion floor   |

Trend checks (least-squares fit over the accumulated post-FATAL history):
`MEMORY_USED` (>= 5% rise alerts as a possible memory leak), `CurrBuffs`
(>= 20% rise alerts as a possible buffer leak), while `NON_VOLATILE_FREE` and
the cycle-slip counters are trended for the record without trend alerts.

Thresholds and trend constants are defined at the top of
[`scripts/soak_monitor.py`](scripts/soak_monitor.py).

## Stress spike

The spike is the installed rotation script in one-shot mode
(`$INSTALL_DIR/flight-ops-rotation.sh spike`) - the same `fprime-cli` command
path and standard `Svc/Subtopologies` command names as the between-cron
nominal loop, so there is exactly one command-sending mechanism. It fires:

- **Command burst saturation** - back-to-back no-op commands with no pacing
  (`SPIKE_BURST_COUNT`, default 25) to saturate `CmdDispatcher`, followed by
  a final commanded round trip proving recovery.
- **File downlink burst** (Linux platforms only) - two back-to-back
  `FileHandling.fileDownlink.SendFile` transfers stressing `BufferManager`
  and disk I/O. Skipped on `pico2`.
- **Parameter persistence churn** (both platforms) - repeated
  `FileHandling.prmDb.PRM_SAVE_FILE` rounds exercising the non-volatile write
  path.

The burst is sized to saturate queues without forcing hard buffer exhaustion,
because the monitor treats any `NoBuffs`/`EmptyBuffs` sample above zero as a
failure.
