# nasa/fprime-actions/soak-test

The `soak-test` action runs the periodic half of a soak: analyze telemetry
captured by the persistent FSW + GDS services for health/resource/stability
problems, then run the deployment's integration tests against the still-running
GDS.

It pairs with [`soak-setup`](../soak-setup/) and assumes its conventions: an
install tree at `$HOME/fprime-soak-<deployment-name>/{bin,dict,gds-logs,ComLoggerFiles,test,venv}`
and a persistent `soak.log`, all namespaced by `deployment-name`.

What it does, in order:

1. Analyzes accumulated telemetry with
   [`scripts/soak_monitor.py`](scripts/soak_monitor.py): raises alerts for FSW
   FATAL/WARNING_HI/WARNING_LO events, per-sample resource threshold breaches,
   and slow leak trends (see below).
2. Runs `pytest` against `$HOME/fprime-soak-<deployment-name>/test/` using the
   soak venv, the installed dictionary, and the deployment's namespaced ZMQ
   sockets. A deployment-shipped `int_config.json` is forwarded to pytest if
   present.

Any FATAL/WARNING event, threshold breach, leak trend, or pytest failure fails
the step.

## Inputs

| Input             | Default      | Description                                                              |
|-------------------|--------------|--------------------------------------------------------------------------|
| `deployment-name` | *(required)* | Must match the `deployment-name` given to `soak-setup` for correct namespacing. |

## Usage

```yaml
- uses: nasa/fprime-actions/soak-test@devel
  with:
    deployment-name: my-deployment
```

## Telemetry sources

The monitor decodes two sources, GDS-first: it parses the GDS text logs
(`channel.log` / `event.log`) written by the persistent GDS service, and only
falls back to decoding `Svc::ComLogger` `.com` files if the GDS logs yield
nothing. Trend samples, FSW events, and monitor-derived alerts all accumulate
in the persistent `soak.log`; the GDS logs are truncated each run.

## Analysis

Channels are classified by their trailing name segment (exact suffix match, e.g.
`...systemResources.MEMORY_USED`). Two kinds of checks run:

### Per-sample threshold checks

An alert fires the moment any sample in the window breaches its rule.

| Channel suffix      | Rule                          | Alert                       |
|---------------------|-------------------------------|-----------------------------|
| `CPU`               | `> 95%`                       | High average CPU usage      |
| `NoBuffs`           | `> 0`                         | Buffer allocation failure   |
| `EmptyBuffs`        | `> 0`                         | Empty buffer returned       |
| `NON_VOLATILE_FREE` | `< 1 GiB`                     | Storage depletion floor     |

### Trend (leak) checks

Trend-tracked channels (`MEMORY_USED`, `NON_VOLATILE_FREE`, `CurrBuffs`) are fit
with a least-squares slope over the full accumulated history (samples before the
last FATAL are dropped as restart noise). A leak alert fires only when the fit is
strong (R² ≥ 0.7) *and* the fitted first-to-last rise clears the channel's
threshold:

| Channel suffix | Concerning direction | Threshold | Alert                 |
|----------------|----------------------|-----------|-----------------------|
| `MEMORY_USED`  | rising               | ≥ 5%      | Possible memory leak  |
| `CurrBuffs`    | rising               | ≥ 20%     | Possible buffer leak  |

`NON_VOLATILE_FREE` is trended for the summary table but only alerts via the
threshold floor above, not the trend. A trend needs at least 10 samples before
it is evaluated; below that it shows as `WAITING`.

All thresholds (percentages, the CPU ceiling, the storage floor, the minimum
sample count, and the R² gate) are defined as constants at the top of
[`scripts/soak_monitor.py`](scripts/soak_monitor.py).
