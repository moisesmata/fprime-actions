#!/usr/bin/env python3
"""F´ Soak Test Monitor.

Each soak-test run reads GDS channel.log + event.log (which contain only
samples since the last cron tick), appends a structured record for
everything we care about to soak-database.log, and truncates the GDS
logs so the next run starts with an empty window.

Trend analysis runs against the FULL soak-database history of telemetry
rows (T records), so slow leaks over many hours are still visible even
though each cron only sees ~30 minutes of fresh samples.

Channels we analyze (units from Svc/SystemResources.fpp and
Svc/BufferManager/Telemetry.fppi):
  SystemResources:
    MEMORY_USED       (U64 KB) - process+system RAM in use; trended for leaks (>=5%).
    NON_VOLATILE_FREE (U64 KB) - free disk on /; absolute floor at 1 GiB.
    CPU               (F32 %)  - average load across all cores; threshold 95%.
    CPU_NN            (F32 %)  - per-core load; collected, not analyzed.
  BufferManager (one set per managed pool, e.g. ComCcsds.commsBufferManager.*):
    CurrBuffs  (U32) - currently allocated; trended for leaks (>=20%).
    NoBuffs    (U32) - allocation failures; >0 alerts.
    EmptyBuffs (U32) - null/zero-size returns; >0 alerts.

soak-database.log row formats (tab-separated, append-only):

  # SOAK STARTED <iso>                       header line (written by setup.sh)
  E\t<iso>\t<severity>\t<name>\t<body>      FSW event we alerted on
  A\t<iso>\t<severity>\t<message>           monitor-derived alert (threshold or trend)
  T\t<iso>\t<channel>\t<value>              raw trend-channel sample

Trend analysis reuses T rows; threshold and event alerts use the fresh
GDS logs only. Threshold breaches are coalesced to ONE alert per channel
per run (with the extreme value + timestamp), so the printed Alerts
list mirrors the printed Threshold checks list one-to-one.

Restart protection: if the database contains any E row at FATAL
severity, trend analysis ignores all T rows (and current-window trend
samples) older than the latest FATAL timestamp. Restarts often produce
huge step changes in memory/buffer counters that would otherwise drown
the regression. T rows are still persisted for the historical record.
"""

import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fprime_gds.executables.cli import ParserBase, StandardPipelineParser

from comlog_parser import process_com_logs
from gdslog_parser import process_gds_logs

MIN_POINTS_FOR_TREND = 10                       # need a few samples before a trend is meaningful
MIN_TREND_R_SQUARED = 0.7                       # below this the slope is noise, not a trend
HIGH_CPU_PERCENT = 95.0                         # hard ceiling on the average CPU channel
NON_VOLATILE_FREE_FLOOR_KB = 1 * 1024 * 1024    # 1 GiB; alert if any sample drops below this
MEMORY_LEAK_PERCENT = 5.0                       # MEMORY_USED rose by >= 5% across the trend window -> alert
BUFFER_LEAK_PERCENT = 20.0                      # CurrBuffs rose by >= 20% across the trend window -> alert

FSW_ALERT_SEVERITIES = ("FATAL", "WARNING_HI", "WARNING_LO")
TELEMETRY_WARNING = "Telemetry Warning"
FAILING_SEVERITIES = FSW_ALERT_SEVERITIES + (TELEMETRY_WARNING,)

# Trend-tracked channel suffixes. Per-sample thresholds and trend rules
# are hardcoded into the if/elif chains in analyze().
TREND_SUFFIXES = ("MEMORY_USED", "NON_VOLATILE_FREE", "CurrBuffs")

# SystemResources telemetry is in KB. Auto-pick MB or GB for display.
MEMORY_SUFFIXES = ("MEMORY_USED", "MEMORY_TOTAL", "NON_VOLATILE_TOTAL", "NON_VOLATILE_FREE")
KB_PER_MB = 1024
KB_PER_GB = 1024 * 1024


def _format_value(suffix: str, value: float) -> str:
    """Channel value: auto-scaled MB/GB for KB channels, :g elsewhere."""
    if suffix not in MEMORY_SUFFIXES:
        return f"{value:g}"
    if abs(value) >= KB_PER_GB:
        return f"{value / KB_PER_GB:.2f} GB"
    return f"{value / KB_PER_MB:.2f} MB"


def _linear_regression(xs: List[float], ys: List[float]) -> Tuple[float, float, float, float]:
    """Least-squares fit y = slope * x + intercept over the given (x, y) lists.

    Returns (slope, intercept, r², residual_σ). r² is 0 when constant;
    σ uses Bessel-style (n-2) denominator and is 0 for n < 3.
    """
    n = len(ys)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    syy = sum((y - mean_y) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0, mean_y, 0.0, 0.0
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    r_squared = (sxy * sxy) / (sxx * syy)
    sigma = 0.0
    if n > 2:
        ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
        sigma = math.sqrt(ss_res / (n - 2))
    return slope, intercept, r_squared, sigma


_SOAK_HEADER_RE = re.compile(r"^# SOAK STARTED (.+)$")


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _format_elapsed(timestamp: str, soak_start: Optional[datetime]) -> str:
    """'(X weeks, Y days, Z hours, W minutes since soak start)' or ''."""
    if soak_start is None or not timestamp:
        return ""
    when = _parse_iso(timestamp)
    if when is None:
        return ""
    delta = when - soak_start
    if delta.total_seconds() < 0:
        return ""
    minutes_total = int(delta.total_seconds() // 60)
    weeks,  minutes_total = divmod(minutes_total, 7 * 24 * 60)
    days,   minutes_total = divmod(minutes_total, 24 * 60)
    hours,  minutes       = divmod(minutes_total, 60)
    plural = lambda n, label: f"{n} {label}{'s' if n != 1 else ''}"
    parts = [plural(n, label) for n, label in
             ((weeks, "week"), (days, "day"), (hours, "hour")) if n]
    parts.append(plural(minutes, "minute"))
    return f" ({', '.join(parts)} since soak start)"


class Results:
    """In-memory state for one soak-monitor run: parser output, derived
    analysis, and the database records we will append."""

    def __init__(self):
        self.channels: Dict[str, List[Tuple[float, str]]] = {}      # name -> [(value, ts)]
        self.events: List[Tuple[str, str, str, str]] = []           # [(severity, name, body, ts)]
        self.alerts: List[Tuple[str, str, str]] = []                # [(severity, msg, ts)]
        # Structured trend rows for the table:
        #   (status, channel, start, end, pct, slope_str, r2, sigma_str)
        self.trend_rows: List[Tuple[str, str, str, str, float, str, float, str]] = []
        # Threshold-breach summary lines (one per breached channel).
        self.threshold_lines: List[str] = []
        self.db_records: List[str] = []                             # rows to append to soak-database.log

    def add_event(self, name: str, severity: str, body: str, ts: str = ""):
        self.events.append((severity.removeprefix("EventSeverity."), name, body, ts))

    def add_channel(self, name: str, value: float, ts: str = ""):
        self.channels.setdefault(name, []).append((value, ts))

    def emit_alert(self, severity: str, message: str, timestamp: str,
                   db_row: str) -> None:
        """Record an alert and the matching database row in one call."""
        self.alerts.append((severity, message, timestamp))
        self.db_records.append(db_row)


def _read_database(path: Path) -> Tuple[
    Dict[str, List[Tuple[float, str]]], Optional[datetime], Optional[datetime], int
]:
    """Walk the soak database. Return:
        (T history, soak_start, latest_fatal_ts, old_alert_count)
    old_alert_count is the count of A + E rows already on disk, used to
    show "Alerts (old)" in the run summary."""
    history: Dict[str, List[Tuple[float, str]]] = {}
    soak_start: Optional[datetime] = None
    latest_fatal: Optional[datetime] = None
    old_alerts = 0
    if not path.exists():
        return history, soak_start, latest_fatal, old_alerts
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line:
            continue
        header = _SOAK_HEADER_RE.match(line)
        if header:
            soak_start = _parse_iso(header.group(1).strip()) or soak_start
            continue
        parts = line.split("\t")
        if not parts:
            continue
        tag = parts[0]
        if tag == "T" and len(parts) >= 4:
            try:
                history.setdefault(parts[2], []).append((float(parts[3]), parts[1]))
            except ValueError:
                pass
        elif tag == "E" and len(parts) >= 5:
            old_alerts += 1
            if parts[2] == "FATAL":
                when = _parse_iso(parts[1])
                if when is not None and (latest_fatal is None or when > latest_fatal):
                    latest_fatal = when
        elif tag == "A":
            old_alerts += 1
    return history, soak_start, latest_fatal, old_alerts


def _truncate_gds_logs(gds_logs: Optional[Path]) -> None:
    """Reset event.log and channel.log to size 0. GDS's append-mode handle
    keeps writing past the truncation point with no NUL gap."""
    if gds_logs is None or not gds_logs.is_dir():
        return
    for path in sorted(gds_logs.glob("**/event.log")) + sorted(gds_logs.glob("**/channel.log")):
        try:
            os.truncate(path, 0)
        except OSError as exc:
            print(f"Warning: could not truncate {path}: {exc}")


# Threshold rules: suffix -> (predicate, breach_label, extreme_direction, value_suffix).
# extreme_direction: "max" means worse = higher; "min" means worse = lower.
_THRESHOLD_RULES = (
    ("CPU",        lambda v: v > HIGH_CPU_PERCENT,           "High average CPU usage",   "max", "%"),
    ("NoBuffs",    lambda v: v > 0,                          "Buffer allocation failure", "max", ""),
    ("EmptyBuffs", lambda v: v > 0,                          "Empty buffer returned",    "max", ""),
    ("NON_VOLATILE_FREE", lambda v: v < NON_VOLATILE_FREE_FLOOR_KB,
                                                              "Storage depletion floor",  "min", ""),
)


def _format_threshold_value(suffix: str, value: float, unit: str) -> str:
    """Threshold value display. Memory-style suffixes get KB->MB/GB scaling;
    everything else gets a bare :g, optionally with a unit suffix (e.g. %)."""
    if suffix in MEMORY_SUFFIXES:
        return _format_value(suffix, value)
    return f"{value:g}{unit}"


def analyze(results: Results, history: Dict[str, List[Tuple[float, str]]],
            latest_fatal: Optional[datetime]) -> None:
    """Populate results.alerts, results.trend_rows, results.threshold_lines,
    and results.db_records.

    FSW events and threshold breaches come from THIS WINDOW. Trend analysis
    runs against the FULL HISTORY (post-FATAL window only)."""

    # FSW events. Track FATAL timestamps to extend the trend cutoff.
    window_fatal = latest_fatal
    for severity, event_name, body, timestamp in results.events:
        if severity in FSW_ALERT_SEVERITIES:
            results.emit_alert(
                severity, f"{event_name}: {body}", timestamp,
                f"E\t{timestamp}\t{severity}\t{event_name}\t{body}",
            )
            if severity == "FATAL":
                when = _parse_iso(timestamp)
                if when is not None and (window_fatal is None or when > window_fatal):
                    window_fatal = when

    # Walk this window's channels.
    for channel_name, samples in results.channels.items():
        suffix = channel_name.rsplit(".", 1)[-1]

        # Trend channel: drain into history + DB.
        if suffix in TREND_SUFFIXES:
            for value, ts in samples:
                history.setdefault(channel_name, []).append((value, ts))
                results.db_records.append(f"T\t{ts}\t{channel_name}\t{value:g}")

        # Threshold check: coalesce to one alert per channel per run,
        # surfacing the extreme value (worst-case) and its timestamp.
        for rule_suffix, predicate, label, direction, unit in _THRESHOLD_RULES:
            if suffix != rule_suffix:
                continue
            breaches = [(v, t) for v, t in samples if predicate(v)]
            if not breaches:
                continue
            picker = max if direction == "max" else min
            extreme_value, extreme_ts = picker(breaches, key=lambda b: b[0])
            value_str = _format_threshold_value(suffix, extreme_value, unit)
            count = len(breaches)
            line = (f"{channel_name}: peak {value_str} at {extreme_ts} "
                    f"({count} sample{'s' if count != 1 else ''} breached)")
            results.threshold_lines.append(line)
            msg = f"{label}: {line}"
            results.emit_alert(TELEMETRY_WARNING, msg, extreme_ts,
                f"A\t{extreme_ts}\t{TELEMETRY_WARNING}\t{msg}")
            break

    # Trend analysis over the full accumulated history (post-FATAL window).
    for channel_name, samples in history.items():
        suffix = channel_name.rsplit(".", 1)[-1]
        if suffix not in TREND_SUFFIXES:
            continue
        is_alert_eligible = suffix in ("MEMORY_USED", "CurrBuffs")

        if window_fatal is not None:
            samples = [(v, t) for v, t in samples
                       if (_parse_iso(t) or window_fatal) >= window_fatal]
        if len(samples) < MIN_POINTS_FOR_TREND:
            continue

        first_dt = _parse_iso(samples[0][1])
        if first_dt is None:
            continue
        xs: List[float] = []
        ys: List[float] = []
        for v, t in samples:
            dt = _parse_iso(t)
            if dt is None:
                continue
            xs.append((dt - first_dt).total_seconds())
            ys.append(v)
        if len(ys) < MIN_POINTS_FOR_TREND or xs[-1] == xs[0]:
            continue

        slope, intercept, r_squared, sigma = _linear_regression(xs, ys)
        n = len(ys)
        slope_per_hour = slope * 3600.0
        fitted_first = intercept
        fitted_last = intercept + slope * xs[-1]
        displacement = abs(fitted_last - fitted_first)
        denominator = abs(fitted_first) or abs(fitted_last) or 1.0
        percent_change = (fitted_last - fitted_first) / denominator * 100.0

        is_kb = suffix in MEMORY_SUFFIXES
        if is_kb:
            slope_str = f"{slope_per_hour / KB_PER_MB:+.4g} MB/hr"
            sigma_str = f"{sigma / KB_PER_MB:.2f} MB"
        else:
            slope_str = f"{slope_per_hour:+.4g}/hr"
            sigma_str = f"{sigma:g}"

        # Decide status before deciding what to do with it.
        status = "OK"
        prefix = ""
        if is_alert_eligible and r_squared >= MIN_TREND_R_SQUARED and displacement >= sigma:
            if suffix == "MEMORY_USED" and percent_change >= MEMORY_LEAK_PERCENT:
                prefix = "Possible memory leak"
            elif suffix == "CurrBuffs" and percent_change >= BUFFER_LEAK_PERCENT:
                prefix = "Possible buffer leak"
        if prefix:
            status = "ALERT"
            ts = samples[-1][1]
            msg = (f"{prefix}: {channel_name}: "
                   f"{_format_value(suffix, ys[0])} -> {_format_value(suffix, ys[-1])} "
                   f"(fit: {percent_change:+.1f}% over {n} samples, "
                   f"slope={slope_str}, R²={r_squared:.2f}, σ={sigma_str})")
            results.emit_alert(TELEMETRY_WARNING, msg, ts,
                f"A\t{ts}\t{TELEMETRY_WARNING}\t{msg}")

        # Always record the trend row, alert or not.
        results.trend_rows.append((
            status, channel_name,
            _format_value(suffix, ys[0]), _format_value(suffix, ys[-1]),
            percent_change, slope_str, r_squared, sigma_str,
        ))


def _print_trend_table(rows, indent: str = "  ") -> None:
    """Render trend rows as an aligned table."""
    if not rows:
        return
    headers = ("STATUS", "CHANNEL", "START", "END", "Δ", "SLOPE", "R²", "σ")
    # Pre-render each cell so we can compute column widths.
    cells = [headers]
    for status, channel, start_s, end_s, pct, slope_s, r2, sigma_s in rows:
        cells.append((
            f"[{status}]", channel, start_s, end_s,
            f"{pct:+.1f}%", slope_s, f"{r2:.2f}", sigma_s,
        ))
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    sep = "  "
    for i, row in enumerate(cells):
        line = sep.join(cell.ljust(widths[col]) for col, cell in enumerate(row))
        print(f"{indent}{line}")
        if i == 0:  # underline header
            print(f"{indent}{sep.join('-' * w for w in widths)}")


def _print_summary(results: Results, soak_start: Optional[datetime],
                   old_alerts: int) -> None:
    total_samples = sum(len(samples) for samples in results.channels.values())
    print("")
    if soak_start is not None:
        print(f"Soak Started:             {soak_start.isoformat()}")
    print(f"Events Decoded:           {len(results.events)}")
    print(f"Channel Samples Decoded:  {total_samples}")
    print(f"Alerts (new):             {len(results.alerts)}")
    print(f"Alerts (old):             {old_alerts}")

    if not (results.trend_rows or results.threshold_lines or results.alerts):
        return
    print("")
    print("Telemetry Analysis:")
    if results.trend_rows:
        print(" Trend checks:")
        _print_trend_table(results.trend_rows, indent="  ")
    if results.threshold_lines:
        print(" Threshold checks:")
        for line in results.threshold_lines:
            print(f"  {line}")
    if results.alerts:
        print(" Alerts:")
        for severity, message, timestamp in results.alerts:
            ts = f" [{timestamp}]" if timestamp else ""
            print(f"  {severity} - {message}{ts}{_format_elapsed(timestamp, soak_start)}")


class SoakArgs(ParserBase):
    DESCRIPTION = "F´ Soak Test Monitor"
    def get_arguments(self):
        return {
            ("--com-logs",): {"type": Path, "default": None,
                "help": "Directory of Svc::ComLogger .com files (may be empty)."},
            ("--gds-logs",): {"type": Path, "default": None,
                "help": "Directory of GDS session logs containing channel.log/event.log."},
            ("--soak-database",): {"type": Path, "default": None,
                "help": "Append-only soak database file. Trend channel samples, "
                        "FSW events, and monitor-derived alerts all accumulate here. "
                        "GDS event.log/channel.log get truncated each run; this file "
                        "is the persistent record. Created by soak-setup."},
        }
    def handle_arguments(self, args, **_):
        if args.com_logs is None and args.gds_logs is None:
            raise SystemExit("error: at least one of --com-logs or --gds-logs is required")
        return args


def main():
    args, _ = ParserBase.parse_args([StandardPipelineParser, SoakArgs])
    results = Results()

    gds_rows = process_gds_logs(args.gds_logs, results)
    if args.com_logs is not None:
        if gds_rows == 0:
            process_com_logs(args, args.com_logs, results)
        else:
            print("GDS logs supplied analysis, skipping ComLogger parsing.")

    history: Dict[str, List[Tuple[float, str]]] = {}
    soak_start: Optional[datetime] = None
    latest_fatal: Optional[datetime] = None
    old_alerts = 0
    if args.soak_database is not None:
        history, soak_start, latest_fatal, old_alerts = _read_database(args.soak_database)

    analyze(results, history, latest_fatal)

    if args.soak_database is not None and results.db_records:
        with args.soak_database.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(results.db_records) + "\n")
    _truncate_gds_logs(args.gds_logs)

    _print_summary(results, soak_start, old_alerts)
    sys.exit(1 if any(s in FAILING_SEVERITIES for s, _, _ in results.alerts) else 0)


if __name__ == "__main__":
    main()
