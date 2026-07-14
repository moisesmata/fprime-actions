# nasa/fprime-actions/soak-summary

The `soak-summary` action reads the persistent `soak.log` left by
[`soak-setup`](../soak-setup/) / [`soak-test`](../soak-test/) and prints a
formatted, human-readable summary of the whole soak so far: start time, elapsed
duration, row counts, and a chronological timeline of every FSW event and
monitor alert.

It pairs with [`soak-setup`](../soak-setup/) and assumes its conventions: an
install tree at `$HOME/fprime-soak-<deployment-name>/` containing an append-only
`soak.log`, namespaced by `deployment-name`. The action fails if that log is not
found.

## Inputs

| Input             | Default      | Description                                                              |
|-------------------|--------------|--------------------------------------------------------------------------|
| `deployment-name` | *(required)* | Must match the `deployment-name` given to `soak-setup` for correct namespacing. |

## Usage

```yaml
- uses: nasa/fprime-actions/soak-summary@devel
  with:
    deployment-name: my-deployment
```

## soak.log format

`soak.log` is tab-separated and append-only, written by
[`soak-test`](../soak-test/)'s `soak_monitor.py`. The summary consumes these row
types:

| Row | Fields                                        | Meaning                                  |
|-----|-----------------------------------------------|------------------------------------------|
| `#` | `# SOAK STARTED <iso>`                         | Header; marks the soak start time.       |
| `E` | `E \t <iso> \t <severity> \t <name> \t <body>` | FSW event the monitor alerted on.        |
| `A` | `A \t <iso> \t <severity> \t <message>`        | Monitor-derived alert (threshold/trend). |
| `T` | `T \t <iso> \t <channel> \t <value>`           | Raw trend-channel sample (counted only). |

## Output

The script ([`scripts/soak_summary.py`](scripts/soak_summary.py)) prints:

1. **Header** — soak start, latest entry, summary run time, and total soak
   duration.
2. **Counts** — number of FSW events (`E`), monitor alerts (`A`), and telemetry
   samples (`T`) across all channels; malformed lines are counted and reported.
3. **Timeline** — every event and alert in chronological order, each annotated
   with elapsed time since soak start. Entries with unparseable timestamps are
   listed separately.
