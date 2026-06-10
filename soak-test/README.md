# nasa/fprime-actions/soak-test

The `soak-test` action runs the periodic half of a soak: analyze data captured
by the persistent FSW + GDS services for health/resource/stability problems,
then run the deployment's integration tests against the still-running GDS.

It pairs with [`soak-deploy`](../soak-deploy/) and assumes its conventions
(`$HOME/fprime-soak/{venv,dict,ComLoggerFiles,test}`, FSW systemd service
`fprime-soak-fsw`). No inputs.

What it does, in order:

1. Verifies `fprime-soak-fsw` is active.
2. Decodes `Svc::ComLogger` `.com` files with `fprime-gds`, raises alerts for
   FATAL/WARNING events and resource thresholds, and runs trend analysis over
   every numeric channel to catch slow degradations (memory leak, draining
   buffer pool, climbing CPU, growing queue depth).
3. Runs `pytest` against `$HOME/fprime-soak/test/` with the installed
   dictionary, using the soak venv.

Any FATAL event, threshold breach, or pytest failure fails the step.

## Usage

```yaml
- uses: nasa/fprime-actions/soak-test@devel
```

## Trend analysis

Every numeric channel time-series is fit with a least-squares slope and
compared first-to-last. Channels are classified by name (case-insensitive
substring) to distinguish "a number that changed" from "a problem":

| Channel pattern (case-insensitive)              | Concerning direction | Alert                       |
|-------------------------------------------------|----------------------|-----------------------------|
| `MEMORY_USED` (Os::SystemResources, KB)         | rising               | possible memory leak        |
| `NON_VOLATILE_FREE`, `HiBuffs` / `LoBuffs`      | falling              | possible resource depletion |
| `systemResources.CPU`, `CPU_NN` (percent)       | rising               | rising CPU trend            |
| `comQueueDepth`, `buffQueueDepth`               | rising               | rising queue depth          |

`MEMORY_TOTAL`, `NON_VOLATILE_TOTAL`, and rate-group timing channels
(`RgMaxTime`) are recorded but do not raise trend alerts. CPU channels also
trigger an instantaneous alert above 90%.

Thresholds (growth/drop percentages and the minimum sample count) are defined
as constants at the top of [`scripts/soak_monitor.py`](scripts/soak_monitor.py).
