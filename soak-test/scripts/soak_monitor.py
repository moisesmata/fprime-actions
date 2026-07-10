#!/usr/bin/env python3
"""F´ Soak Test Monitor.

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

soak.log row formats (tab-separated, append-only persistent soak log):

  # SOAK STARTED <iso>                       header line (written by setup.sh)
  E\t<iso>\t<severity>\t<name>\t<body>      FSW event we alerted on
  A\t<iso>\t<severity>\t<message>           monitor-derived alert (threshold or trend)
  T\t<iso>\t<channel>\t<value>              raw trend-channel sample
"""

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

# Trend-tracked channel suffixes. Those in LEAK_THRESHOLDS also get leak-alert
# checks; NON_VOLATILE_FREE is only trended (and threshold-checked below).
# Per-sample threshold rules live in THRESHOLD_RULES.
TREND_SUFFIXES = ("MEMORY_USED", "NON_VOLATILE_FREE", "CurrBuffs")

# Trend suffix -> (alert label, min percent rise to alert). A trend only alerts
# when its fit is strong (see MIN_TREND_R_SQUARED) and rises by at least this.
LEAK_THRESHOLDS = {
    "MEMORY_USED": ("Possible memory leak", MEMORY_LEAK_PERCENT),
    "CurrBuffs":   ("Possible buffer leak", BUFFER_LEAK_PERCENT),
}

# SystemResources telemetry is in KB. Auto-pick MB or GB for display.
MEMORY_SUFFIXES = ("MEMORY_USED", "MEMORY_TOTAL", "NON_VOLATILE_TOTAL", "NON_VOLATILE_FREE")
KB_PER_MB = 1024
KB_PER_GB = 1024 * 1024

# Matches the soak.log header line written by setup.sh.
SOAK_HEADER_RE = re.compile(r"^# SOAK STARTED (.+)$")


def format_value(suffix: str, value: float) -> str:
    """Channel value: auto-scaled MB/GB for KB channels, :g elsewhere."""
    if suffix not in MEMORY_SUFFIXES:
        return f"{value:g}"
    if abs(value) >= KB_PER_GB:
        return f"{value / KB_PER_GB:.2f} GB"
    return f"{value / KB_PER_MB:.2f} MB"


def linear_regression(xs: List[float], ys: List[float]) -> Tuple[float, float, float]:
    """Least-squares fit y = slope * x + intercept over the given (x, y) lists.

    Returns (slope, intercept, r²). r² is 0 when constant.
    """
    n = len(ys)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    syy = sum((y - mean_y) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0, mean_y, 0.0
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    r_squared = (sxy * sxy) / (sxx * syy)
    return slope, intercept, r_squared


def parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def format_duration(seconds: float) -> str:
    """Compact human-readable duration (e.g. '2d 4h', '36m 12s')."""
    s = max(0, int(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def format_elapsed(timestamp: str, soak_start: Optional[datetime]) -> str:
    """'(X weeks, Y days, Z hours, W minutes since soak start)' or ''."""
    if soak_start is None or not timestamp:
        return ""
    when = parse_iso(timestamp)
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
    analysis, and the persistent-log rows we will append."""

    def __init__(self):
        self.channels: Dict[str, List[Tuple[float, str]]] = {}      # name -> [(value, ts)]
        self.events: List[Tuple[str, str, str, str]] = []           # [(severity, name, body, ts)]
        self.alerts: List[Tuple[str, str, str]] = []                # [(severity, msg, ts)]
        # Trend-table rows: every observed trend-tracked channel produces a row
        # (status, channel, start, end, pct_str, slope_str, r2_str, time_span_str)
        self.trend_rows: List[Tuple[str, str, str, str, str, str, str, str]] = []
        # Threshold-check rows: every observed threshold-tracked channel produces a row
        # (status, channel, extreme_value_str, extreme_timestamp_str, note_str)
        self.threshold_rows: List[Tuple[str, str, str, str, str]] = []
        self.log_records: List[str] = []                            # rows to append to soak.log

    def add_event(self, name: str, severity: str, body: str, ts: str = ""):
        self.events.append((severity.removeprefix("EventSeverity."), name, body, ts))

    def add_channel(self, name: str, value: float, ts: str = ""):
        self.channels.setdefault(name, []).append((value, ts))

    def add_alert(self, severity: str, message: str, timestamp: str,
                  log_row: str) -> None:
        """Record an alert and the matching soak.log row in one call."""
        self.alerts.append((severity, message, timestamp))
        self.log_records.append(log_row)

    def add_telemetry_warning(self, message: str, timestamp: str) -> None:
        """Emit a monitor-derived alert as both an alert and an 'A' log row."""
        self.add_alert(TELEMETRY_WARNING, message, timestamp,
                       f"A\t{timestamp}\t{TELEMETRY_WARNING}\t{message}")


def read_soak_log(path: Path) -> Tuple[
    Dict[str, List[Tuple[float, str]]], Optional[datetime], Optional[datetime], int
]:
    """Walk the persistent soak log. Return:
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
        header = SOAK_HEADER_RE.match(line)
        if header:
            soak_start = parse_iso(header.group(1).strip()) or soak_start
            continue
        parts = line.split("\t")
        tag = parts[0] if parts else ""
        if tag == "T" and len(parts) >= 4:
            try:
                history.setdefault(parts[2], []).append((float(parts[3]), parts[1]))
            except ValueError:
                pass
        elif tag == "A":
            old_alerts += 1
        elif tag == "E" and len(parts) >= 5:
            old_alerts += 1
            if parts[2] == "FATAL":
                when = parse_iso(parts[1])
                if when is not None and (latest_fatal is None or when > latest_fatal):
                    latest_fatal = when
    return history, soak_start, latest_fatal, old_alerts


def truncate_gds_logs(gds_logs: Optional[Path]) -> None:
    """Reset event.log and channel.log to size 0. GDS's append-mode handle
    keeps writing past the truncation point with no NUL gap."""
    if gds_logs is None or not gds_logs.is_dir():
        return
    for path in sorted(gds_logs.glob("**/event.log")) + sorted(gds_logs.glob("**/channel.log")):
        try:
            os.truncate(path, 0)
        except OSError as exc:
            print(f"Warning: could not truncate {path}: {exc}")


# Per-sample threshold rules: (suffix, predicate, label, direction, unit).
# direction: "max" means worse = higher; "min" means worse = lower.
THRESHOLD_RULES = (
    ("CPU",        lambda v: v > HIGH_CPU_PERCENT,           "High average CPU usage",   "max", "%"),
    ("NoBuffs",    lambda v: v > 0,                          "Buffer allocation failure", "max", ""),
    ("EmptyBuffs", lambda v: v > 0,                          "Empty buffer returned",    "max", ""),
    ("NON_VOLATILE_FREE", lambda v: v < NON_VOLATILE_FREE_FLOOR_KB,
                                                              "Storage depletion floor",  "min", ""),
)
THRESHOLD_BY_SUFFIX = {rule[0]: rule for rule in THRESHOLD_RULES}


def format_threshold_value(suffix: str, value: float, unit: str) -> str:
    """Threshold value display. Memory-style suffixes get KB->MB/GB scaling;
    everything else gets a bare :g, optionally with a unit suffix (e.g. %)."""
    if suffix in MEMORY_SUFFIXES:
        return format_value(suffix, value)
    return f"{value:g}{unit}"


def analyze(results: Results, history: Dict[str, List[Tuple[float, str]]],
            latest_fatal: Optional[datetime]) -> None:
    """Populate results.alerts, results.trend_rows, results.threshold_rows,
    and results.log_records.

    FSW events and threshold breaches come from THIS WINDOW. Trend analysis
    runs against the FULL HISTORY (post-FATAL window only)."""
    window_fatal = _analyze_events(results, latest_fatal)
    _analyze_this_window_channels(results, history)
    _analyze_trends(results, history, window_fatal)


def _analyze_events(results: Results,
                    latest_fatal: Optional[datetime]) -> Optional[datetime]:
    """Alert on this window's FSW events. Return the latest FATAL timestamp
    seen (incl. prior history), which bounds the trend window."""
    window_fatal = latest_fatal
    for severity, event_name, body, timestamp in results.events:
        if severity not in FSW_ALERT_SEVERITIES:
            continue
        results.add_alert(
            severity, f"{event_name}: {body}", timestamp,
            f"E\t{timestamp}\t{severity}\t{event_name}\t{body}",
        )
        when = parse_iso(timestamp) if severity == "FATAL" else None
        if when is not None and (window_fatal is None or when > window_fatal):
            window_fatal = when
    return window_fatal


def _analyze_this_window_channels(
        results: Results, history: Dict[str, List[Tuple[float, str]]]) -> None:
    """Drain this window's channels: trend channels append to history +
    soak.log; threshold channels feed threshold-table rows and alerts."""
    for channel_name, samples in results.channels.items():
        suffix = channel_name.rsplit(".", 1)[-1]

        if suffix in TREND_SUFFIXES:
            for value, ts in samples:
                history.setdefault(channel_name, []).append((value, ts))
                results.log_records.append(f"T\t{ts}\t{channel_name}\t{value:g}")

        rule = THRESHOLD_BY_SUFFIX.get(suffix)
        if rule is not None:
            _check_threshold(results, channel_name, suffix, samples, rule)


def _check_threshold(results: Results, channel_name: str, suffix: str,
                     samples: List[Tuple[float, str]], rule) -> None:
    """Evaluate one channel against its per-sample threshold rule, appending a
    threshold-table row and (on breach) an alert."""
    _, predicate, label, direction, unit = rule
    if not samples:
        results.threshold_rows.append(
            ("WAITING", channel_name, "n/a", "n/a", "no samples this window"))
        return

    breaches = [(v, t) for v, t in samples if predicate(v)]
    picker = max if direction == "max" else min
    pool = breaches or samples
    extreme_value, extreme_ts = picker(pool, key=lambda b: b[0])
    value_str = format_threshold_value(suffix, extreme_value, unit)
    verb = "breached" if breaches else "observed"
    note = f"{len(pool)} sample{'s' if len(pool) != 1 else ''} {verb}"

    results.threshold_rows.append(
        ("ALERT" if breaches else "OK", channel_name, value_str, extreme_ts, note))
    if breaches:
        results.add_telemetry_warning(
            f"{label}: {channel_name}: peak {value_str} at {extreme_ts} ({note})",
            extreme_ts)


def _trend_series(
        samples: List[Tuple[float, str]], window_fatal: Optional[datetime]
) -> Tuple[List[float], List[float], List[str]]:
    """Convert (value, iso-ts) samples into parallel (xs, ys, timestamps) lists
    where xs is seconds since the first sample. Drops samples before the latest
    FATAL (restart protection) and any with unparseable timestamps."""
    if window_fatal is not None:
        samples = [(v, t) for v, t in samples
                   if (parse_iso(t) or window_fatal) >= window_fatal]
    first_dt = parse_iso(samples[0][1]) if samples else None
    if first_dt is None:
        return [], [], []
    xs: List[float] = []
    ys: List[float] = []
    timestamps: List[str] = []
    for v, t in samples:
        dt = parse_iso(t)
        if dt is not None:
            xs.append((dt - first_dt).total_seconds())
            ys.append(v)
            timestamps.append(t)
    return xs, ys, timestamps


def _analyze_trends(results: Results, history: Dict[str, List[Tuple[float, str]]],
                    window_fatal: Optional[datetime]) -> None:
    """Least-squares trend analysis over the full accumulated history.
    Channels with too few samples render as WAITING."""
    trend_channels = sorted({c for c in history
                             if c.rsplit(".", 1)[-1] in TREND_SUFFIXES})
    for channel_name in trend_channels:
        suffix = channel_name.rsplit(".", 1)[-1]
        xs, ys, timestamps = _trend_series(history.get(channel_name, []), window_fatal)
        n = len(ys)

        time_span_str = format_duration(xs[-1]) if n >= 2 else "n/a"
        if n < MIN_POINTS_FOR_TREND or (n >= 2 and xs[-1] == xs[0]):
            note = time_span_str if n >= 2 else f"{n}/{MIN_POINTS_FOR_TREND} samples"
            results.trend_rows.append(
                ("WAITING", channel_name) + ("n/a",) * 5 + (note,))
            continue

        slope, intercept, r_squared = linear_regression(xs, ys)
        fitted_first = intercept
        fitted_last = intercept + slope * xs[-1]
        denominator = abs(fitted_first) or abs(fitted_last) or 1.0
        percent_change = (fitted_last - fitted_first) / denominator * 100.0

        slope_per_hour = slope * 3600.0
        if suffix in MEMORY_SUFFIXES:
            slope_str = f"{slope_per_hour / KB_PER_MB:+.4g} MB/hr"
        else:
            slope_str = f"{slope_per_hour:+.4g}/hr"

        status = "OK"
        label, min_percent = LEAK_THRESHOLDS.get(suffix, (None, 0.0))
        leaking = (label is not None and r_squared >= MIN_TREND_R_SQUARED
                   and percent_change >= min_percent)
        if leaking:
            status = "ALERT"
            results.add_telemetry_warning(
                f"{label}: {channel_name}: "
                f"{format_value(suffix, ys[0])} -> {format_value(suffix, ys[-1])} "
                f"(fit: {percent_change:+.1f}% over {n} samples, "
                f"slope={slope_str}, R-squared={r_squared:.2f})",
                timestamps[-1])

        results.trend_rows.append((
            status, channel_name,
            format_value(suffix, ys[0]), format_value(suffix, ys[-1]),
            f"{percent_change:+.1f}%", slope_str, f"{r_squared:.2f}",
            time_span_str,
        ))


def print_table(headers: Tuple[str, ...], rows: List[Tuple[str, ...]],
                 indent: str = "  ") -> None:
    """Render rows as an aligned text table with a header underline."""
    if not rows:
        return
    cells = [headers] + [tuple(c for c in row) for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    sep = "  "
    for i, row in enumerate(cells):
        print(f"{indent}{sep.join(cell.ljust(widths[col]) for col, cell in enumerate(row))}")
        if i == 0:
            print(f"{indent}{sep.join('-' * w for w in widths)}")


TREND_HEADERS = (
    "STATUS", "CHANNEL", "START", "END",
    "PERCENTAGE CHANGE", "SLOPE", "R-SQUARED", "TIME SPAN",
)
THRESHOLD_HEADERS = (
    "STATUS", "CHANNEL", "EXTREME VALUE", "TIMESTAMP", "NOTES",
)


def print_summary(results: Results, soak_start: Optional[datetime],
                   old_alerts: int) -> None:
    total_samples = sum(len(samples) for samples in results.channels.values())
    print("")
    if soak_start is not None:
        print(f"Soak Started:             {soak_start.strftime('%A %B %d %Y %I:%M:%S %p')}")
    print(f"Events Decoded:           {len(results.events)}")
    print(f"Channel Samples Decoded:  {total_samples}")
    print(f"Alerts (new):             {len(results.alerts)}")
    print(f"Alerts (old):             {old_alerts}")

    if not (results.trend_rows or results.threshold_rows or results.alerts):
        return

    print("")
    print("Telemetry Analysis:")
    if results.trend_rows:
        print("")
        print(" Trend checks:")
        print(" (Least squares fit done on telemetry since the last boot sequence)")
        # Bracket the STATUS cell only for visual punch.
        formatted = [(f"[{r[0]}]",) + r[1:] for r in results.trend_rows]
        print_table(TREND_HEADERS, formatted, indent="  ")

    if results.threshold_rows:
        print("")
        print(" Threshold checks:")
        formatted = [(f"[{r[0]}]",) + r[1:] for r in results.threshold_rows]
        print_table(THRESHOLD_HEADERS, formatted, indent="  ")

    if results.alerts:
        print("")
        print(" Alerts:")
        for severity, message, timestamp in results.alerts:
            ts = f" [{timestamp}]" if timestamp else ""
            print(f"  {severity} - {message}{ts}{format_elapsed(timestamp, soak_start)}")
        print("")

    print("")


class SoakArgs(ParserBase):
    DESCRIPTION = "F´ Soak Test Monitor"
    def get_arguments(self):
        return {
            ("--com-logs",): {"type": Path, "default": None,
                "help": "Directory of Svc::ComLogger .com files (may be empty)."},
            ("--gds-logs",): {"type": Path, "default": None,
                "help": "Directory of GDS session logs containing channel.log/event.log."},
            ("--soak-log",): {"type": Path, "default": None,
                "help": "Append-only persistent soak log. Trend channel samples, "
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
    if args.soak_log is not None:
        history, soak_start, latest_fatal, old_alerts = read_soak_log(args.soak_log)

    analyze(results, history, latest_fatal)

    if args.soak_log is not None and results.log_records:
        with args.soak_log.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(results.log_records) + "\n")
    truncate_gds_logs(args.gds_logs)

    print_summary(results, soak_start, old_alerts)
    sys.exit(1 if any(s in FAILING_SEVERITIES for s, _, _ in results.alerts) else 0)


if __name__ == "__main__":
    main()
