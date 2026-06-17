"""Parse GDS text logs (channel.log / event.log).

The GDS writes one CSV row per record into per-session subdirs under its
--logs directory:

  channel.log: ts,(time_tag),channel.name,id,value_with_units
  event.log:   ts,(time_tag),event.name,id,EventSeverity.X,args

`process_gds_logs` walks a logs root, skips empty files, parses both kinds,
and forwards each row to the caller's Results object via add_text_channel /
add_text_event.
"""

import re
from pathlib import Path
from typing import Optional

# Channel values often carry a unit suffix ("13.13 percent"); grab leading number.
_LEADING_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def _parse_text_value(raw: str) -> Optional[float]:
    """Pull the leading number out of a GDS channel value like '13.13 percent'."""
    if raw is None:
        return None
    m = _LEADING_NUM.search(raw)
    return float(m.group(0)) if m else None


def _process_channel_log(path: Path, results) -> int:
    """One CSV row per sample: ts,(time_tag),channel.name,id,value."""
    count = 0
    with path.open("r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",", 4)
            if len(parts) < 5:
                continue
            ts, _time_tag, name, _id, raw_val = parts
            value = _parse_text_value(raw_val)
            if value is None:
                continue
            results.add_text_channel(name, value, ts)
            count += 1
    return count


def _process_event_log(path: Path, results) -> int:
    """One CSV row per event: ts,(time_tag),event.name,id,EventSeverity.X,args."""
    count = 0
    with path.open("r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",", 5)
            if len(parts) < 5:
                continue
            ts, _time_tag, name, _id, severity = parts[:5]
            body = parts[5] if len(parts) >= 6 else ""
            results.add_text_event(name, severity, body, ts)
            count += 1
    return count


def process_gds_logs(gds_logs: Optional[Path], results) -> int:
    """Walk a GDS logs root (e.g. .../Ref/logs) and parse channel.log/event.log
    in every session subdir. Empty files are silently skipped.
    Returns the total number of rows ingested."""
    if gds_logs is None:
        return 0
    if not gds_logs.exists() or not gds_logs.is_dir():
        print(f"GDS logs directory not found: {gds_logs}; skipping GDS analysis.")
        return 0

    channel_logs = sorted(gds_logs.glob("**/channel.log"))
    event_logs = sorted(gds_logs.glob("**/event.log"))
    nonempty_channels = [p for p in channel_logs if p.stat().st_size > 0]
    nonempty_events = [p for p in event_logs if p.stat().st_size > 0]

    if not nonempty_channels and not nonempty_events:
        print(f"No non-empty channel.log/event.log under {gds_logs}; skipping GDS analysis.")
        return 0

    print(f"Processing {len(nonempty_channels)} channel.log and {len(nonempty_events)} "
          f"event.log file(s) from {gds_logs}")
    rows = 0
    for p in nonempty_channels:
        try:
            rows += _process_channel_log(p, results)
        except Exception as exc:
            print(f"Error processing {p}: {exc}")
    for p in nonempty_events:
        try:
            rows += _process_event_log(p, results)
        except Exception as exc:
            print(f"Error processing {p}: {exc}")
    return rows
