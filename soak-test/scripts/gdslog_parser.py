"""Parse GDS text logs (channel.log / event.log).

The GDS writes one CSV row per record into per-session subdirs under its --logs directory:
  channel.log: ts,(time_stamp),channel.name,id,value_with_units
  event.log:   ts,(time_stamp),event.name,id,EventSeverity.X,args
"""
import re
from pathlib import Path
from typing import Optional

# Channel values often carry a unit suffix ("13.13 percent"), grab leading number.
_LEADING_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")

def _parse_channel_log(path: Path, results) -> int:
    row_count = 0
    for line in path.read_text(errors="replace").splitlines():
        parts = line.strip().split(",", 4)
        if len(parts) < 5:
            continue
        timestamp, _time_tag, channel_name, _channel_id, raw_value = parts
        match = _LEADING_NUM.search(raw_value)
        if not match:
            continue
        results.add_channel(channel_name, float(match.group(0)), timestamp)
        row_count += 1
    return row_count

def _parse_event_log(path: Path, results) -> int:
    row_count = 0
    for line in path.read_text(errors="replace").splitlines():
        parts = line.strip().split(",", 5)
        if len(parts) < 5:
            continue
        timestamp, _time_tag, event_name, _event_id, severity = parts[:5]
        body = parts[5] if len(parts) >= 6 else ""
        results.add_event(event_name, severity, body, timestamp)
        row_count += 1
    return row_count

def process_gds_logs(gds_logs: Optional[Path], results) -> int:
    """Walk a GDS logs root (e.g. .../Ref/logs) and parse channel.log/event.log
    in every session subdir. Empty files are skipped.
    Returns the total number of rows ingested."""

    if gds_logs is None:
        return 0
    if not gds_logs.is_dir():
        print(f"GDS logs directory not found: {gds_logs}; skipping GDS analysis.")
        return 0

    channel_log_paths = [path for path in sorted(gds_logs.glob("**/channel.log")) if path.stat().st_size]
    event_log_paths = [path for path in sorted(gds_logs.glob("**/event.log")) if path.stat().st_size]
    if not channel_log_paths and not event_log_paths:
        print(f"No non-empty channel.log/event.log under {gds_logs}; skipping GDS analysis.")
        return 0

    print(f"Processing {len(channel_log_paths)} channel.log and "
          f"{len(event_log_paths)} event.log file(s) from {gds_logs}")
    total_rows = 0
    work_items = ([(path, _parse_channel_log) for path in channel_log_paths] +
                  [(path, _parse_event_log) for path in event_log_paths])
    for path, parser in work_items:
        try:
            total_rows += parser(path, results)
        except Exception as exc:
            print(f"Error processing {path}: {exc}")
    return total_rows
