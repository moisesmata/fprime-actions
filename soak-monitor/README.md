# nasa/fprime-actions/soak-monitor

The `soak-monitor` action analyzes data captured during a soak test for health,
resource, and stability problems. It decodes `Svc::ComLogger` `.com` files with
`fprime-gds`, raises alerts for FATAL/WARNING events and resource thresholds, and
runs **trend analysis** over every numeric channel to catch slow degradations
(e.g. a memory leak or a steadily draining buffer pool). 


## Inputs

| Input           | Default     | Description                                                                                                         |
|-----------------|-------------|---------------------------------------------------------------------------------------------------------------------|
| `dictionary`    | (required)  | Path (glob allowed) to the deployment dictionary JSON used to decode events and channels.                           |
| `com-logs`      | `""`        | Optional directory of `Svc::ComLogger` `.com` files. Read if present; ignored if missing or empty.   |
| `python`        | `python3`   | Python interpreter to run the monitor with. Point at the soak virtualenv to reuse its `fprime-gds`.                 |
| `logs-dir`      | `""`        | Directory used for the monitor's own GDS logging prefix / scratch files. A temp dir is created when empty.          |
| `fail-on-fatal` | `"true"`    | Fail the step when a FATAL event is detected.                                                                       |

## Usage

```yaml
- uses: nasa/fprime-actions/soak-monitor@devel
  with:
    dictionary: "/opt/fprime-soak/dict/*TopologyDictionary.json"
    com-logs: /opt/fprime-soak/ComLoggerFiles
    python: /opt/fprime-soak/venv/bin/python
```

ComLogger output is optional. If your deployment does not run `Svc::ComLogger`,
omit `com-logs` (or point it at an empty directory) and the monitor will report
that there was nothing to analyze and exit successfully.

## Trend analysis

Every numeric channel time-series is fit with a least-squares slope and compared
first-to-last. Channels are classified by name (case-insensitive substring) to
distinguish "a number that changed" from "a problem":

| Pattern                                          | Concerning direction | Alert                       |
|--------------------------------------------------|----------------------|-----------------------------|
| `memory`, `heap`, `ram`, `mem`                   | rising               | possible resource leak      |
| `buffer`, `free`, `empty`, `available`, `remaining` | falling              | possible resource depletion |
| `cpu`, `load`                                    | rising               | rising load trend           |

Thresholds (growth/drop percentages and the minimum sample count) are defined as
constants at the top of [`scripts/soak_monitor.py`](scripts/soak_monitor.py).
