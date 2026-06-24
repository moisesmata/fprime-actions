#!/usr/bin/env python3
"""F´ Soak Test Monitor.

Uses gdslog/comlog_parser.py to parse logs, stores raw events and telemetry
in a Results struct, and during analyze() walks them and emits alerts.

Channels we analyze (units come from Svc/SystemResources.fpp and
Svc/BufferManager/Telemetry.fppi):
  SystemResources:
    MEMORY_USED       (U64 KB) - process+system RAM in use; trended for leaks.
    MEMORY_TOTAL      (U64 KB) - total RAM (constant); not analyzed.
    NON_VOLATILE_FREE (U64 KB) - free disk on /; trended for depletion.
    NON_VOLATILE_TOTAL(U64 KB) - total disk (constant); not analyzed.
    CPU               (F32 %)  - average load across all cores; threshold 95%.
    CPU_NN            (F32 %)  - per-core load; same 95% threshold.
  BufferManager (one set per managed pool, e.g. ComCcsds.commsBufferManager.*):
    TotalBuffs (U32) - total buffers configured (constant); not analyzed.
    CurrBuffs  (U32) - currently allocated; trended (rising = leak).
    HiBuffs    (U32) - high-water mark of CurrBuffs; trended (rising = leak).
    NoBuffs    (U32) - allocation failures (cumulative); >0 alerts.
    EmptyBuffs (U32) - null/zero-size returns (cumulative); >0 alerts.

Trend analysis runs a least-squares linear regression against sample index,
reporting slope, R², and residual σ. Alerts only fire when the fit is
trustworthy (R² ≥ MIN_TREND_R_SQUARED), so a flat-but-noisy series doesn't
trip a leak alert. To keep passing-run output readable, only channels with
a configured trend rule (see TREND_RULES) appear in the trend section —
per-core CPU, constants like NON_VOLATILE_TOTAL, and other numeric
channels are alerted on (where applicable) but not printed as trend lines.

Cross-run dedup: --failure-log <path> names a file the soak setup creates
empty and the soak test appends to. Each alert has a stable key; if a
key is already in the file, that alert is suppressed from this run -
it was reported on a prior cron tick and shouldn't re-fail the build.
A fresh soak setup truncates the file to start over.

Per-sample telemetry alerts (CPU > 95%, NoBuffs/EmptyBuffs > 0) are
coalesced to one summary line per channel per run, so the dedup file
stays tidy and a single bad run doesn't append dozens of keys.
"""

import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from fprime_gds.executables.cli import ParserBase, StandardPipelineParser

from comlog_parser import process_com_logs
from gdslog_parser import process_gds_logs

MIN_POINTS_FOR_TREND = 10        # need a few samples before a trend is meaningful
LEAK_GROWTH_PERCENT = 10.0       # resource grew by this much across the window -> alert
DEPLETION_DROP_PERCENT = 50.0    # free resource dropped by this much -> alert
MIN_TREND_R_SQUARED = 0.5        # below this the slope is noise, not a trend
HIGH_CPU_PERCENT = 95.0          # hard ceiling on CPU and CPU_NN samples (per FPP, F32 percent)

# Channels that get trend regression + a printed trend line + a possible
# alert. Format: suffix -> (direction, threshold_percent, alert_prefix).
# direction "up" alerts when fitted growth >= threshold; "down" when fitted
# drop <= -threshold. Anything not listed here is collected and per-sample
# alerted (where rules apply) but NOT printed in the trend section.
TREND_RULES = {
    "MEMORY_USED":       ("up",   LEAK_GROWTH_PERCENT,    "Possible memory leak"),
    "NON_VOLATILE_FREE": ("down", DEPLETION_DROP_PERCENT, "Possible storage depletion"),
    "CurrBuffs":         ("up",   LEAK_GROWTH_PERCENT,    "Possible buffer leak"),
    "HiBuffs":           ("up",   LEAK_GROWTH_PERCENT,    "Possible buffer leak"),
}

# FSW-emitted event severities (kept verbatim from the FSW dictionary).
FSW_ALERT_SEVERITIES = ("FATAL", "WARNING_HI", "WARNING_LO")
# Monitor-derived alerts use a distinct label so they aren't confused with
# FSW events of the same name.
TELEMETRY_WARNING = "Telemetry Warning"
FAILING_SEVERITIES = FSW_ALERT_SEVERITIES + (TELEMETRY_WARNING,)

# SystemResources telemetry is in KB (Svc/SystemResources.cpp divides bytes
# by 1024 before tlmWrite). Auto-pick MB or GB so a Pi (~500 MB used) and a
# workstation (~63 GB used) both render readably.
MEMORY_SUFFIXES = ("MEMORY_USED", "MEMORY_TOTAL", "NON_VOLATILE_TOTAL", "NON_VOLATILE_FREE")
KB_PER_MB = 1024
KB_PER_GB = 1024 * 1024


def _scale_kb(value_kb: float) -> Tuple[float, str]:
    if abs(value_kb) >= KB_PER_GB:
        return value_kb / KB_PER_GB, "GB"
    return value_kb / KB_PER_MB, "MB"


def _format_value(suffix: str, value: float) -> str:
    if suffix in MEMORY_SUFFIXES:
        scaled, unit = _scale_kb(value)
        return f"{scaled:.2f} {unit}"
    return f"{value:g}"


def _format_slope(suffix: str, slope: float) -> str:
    # Slope is dy/d(sample). For memory channels y is in KB; show MB/sample
    # rather than auto-picking GB (a small leak would round to 0 GB/sample).
    if suffix in MEMORY_SUFFIXES:
        return f"{slope / KB_PER_MB:+.4g} MB/sample"
    return f"{slope:+.4g}/sample"


def _format_sigma(suffix: str, sigma: float) -> str:
    if suffix in MEMORY_SUFFIXES:
        return f"{sigma / KB_PER_MB:.2f} MB"
    return f"{sigma:g}"


def _linear_regression(values: List[float]) -> Tuple[float, float, float, float]:
    """Least-squares fit y = slope * i + intercept over i in [0, n).

    Returns (slope, intercept, r_squared, residual_sigma). r_squared is 0
    when the input is constant; residual sigma uses Bessel-style (n-2)
    denominator and is 0 for n < 3.
    """
    n = len(values)
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    sxx = sum((i - mean_x) ** 2 for i in range(n))
    sxy = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(values))
    syy = sum((y - mean_y) ** 2 for y in values)
    if sxx == 0 or syy == 0:
        return 0.0, mean_y, 0.0, 0.0
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    r_squared = (sxy * sxy) / (sxx * syy)
    if n > 2:
        ss_res = sum((y - (intercept + slope * i)) ** 2 for i, y in enumerate(values))
        sigma = math.sqrt(ss_res / (n - 2))
    else:
        sigma = 0.0
    return slope, intercept, r_squared, sigma


class Alert:
    """One alert plus a stable key for cross-run dedup.

    The key is a tuple stored in the failure log; a future run can recognise
    the same condition and suppress it. Per-incident alerts (FSW events,
    distinct samples) include the timestamp; recurring conditions (a
    channel that breached, a trend) key only on channel + kind so they
    aren't re-flagged every cron tick.
    """
    __slots__ = ("severity", "message", "timestamp", "key")

    def __init__(self, severity: str, message: str, timestamp: str, key: Tuple[str, ...]):
        self.severity = severity
        self.message = message
        self.timestamp = timestamp
        self.key = key

    def key_line(self) -> str:
        return "\t".join((self.severity, *self.key))


class Results:
    """Raw store of decoded events and telemetry samples. analyze() fills in
    self.alerts and self.trends from self.events and self.channels"""

    def __init__(self):
        self.channels: Dict[str, List[Tuple[float, str]]] = {}      # name -> [(value, ts)]
        self.events: List[Tuple[str, str, str, str]] = []           # [(severity, name, body, ts)]
        self.alerts: List[Alert] = []
        self.trends: List[str] = []                                 # one summary line per analyzed series

    def add_event(self, name: str, severity: str, body: str, ts: str = ""):
        self.events.append((severity.removeprefix("EventSeverity."), name, body, ts))

    def add_channel(self, name: str, value: float, ts: str = ""):
        self.channels.setdefault(name, []).append((value, ts))

    def analyze(self):
        # FSW events keep their native severity. Each (event, timestamp) is
        # a distinct incident so the dedup key includes the timestamp.
        for severity, event_name, body, timestamp in self.events:
            if severity in FSW_ALERT_SEVERITIES:
                self.alerts.append(Alert(
                    severity,
                    f"{event_name}: {body}",
                    timestamp,
                    ("event", event_name, timestamp),
                ))

        for channel_name, samples in self.channels.items():
            suffix = channel_name.rsplit(".", 1)[-1]
            # SystemResources exposes CPU (average) and CPU_00, CPU_01, ...
            # per-core. We alert on both at the same threshold but only print
            # a trend line for channels in TREND_RULES (not per-core CPU).
            is_cpu = suffix == "CPU" or suffix.startswith("CPU_")
            values = [value for value, _ in samples]

            # Per-sample threshold checks — coalesce to ONE alert per
            # channel per run, summarising peak + count + first offense.
            # Stable key (kind, channel, label) so once a channel has been
            # flagged future cron ticks don't re-fire on later samples.
            offending: List[Tuple[float, str]] = []
            kind: Tuple[str, ...] = ()
            summary_prefix = ""
            value_fmt = lambda v: f"{v:g}"
            if is_cpu:
                offending = [(v, t) for v, t in samples if v > HIGH_CPU_PERCENT]
                kind = ("threshold", channel_name, "high-cpu")
                summary_prefix = "High CPU usage"
                value_fmt = lambda v: f"{v:g}%"
            elif suffix == "NoBuffs":
                offending = [(v, t) for v, t in samples if v > 0]
                kind = ("threshold", channel_name, "no-buffs")
                summary_prefix = "Buffer allocation failure"
            elif suffix == "EmptyBuffs":
                offending = [(v, t) for v, t in samples if v > 0]
                kind = ("threshold", channel_name, "empty-buffs")
                summary_prefix = "Empty buffer returned"

            if offending:
                peak_value = max(v for v, _ in offending)
                first_value, first_ts = offending[0]
                msg = (
                    f"{summary_prefix}: {channel_name} breached "
                    f"{len(offending)} sample(s); peak {value_fmt(peak_value)}, "
                    f"first {value_fmt(first_value)} at {first_ts}"
                )
                self.alerts.append(Alert(TELEMETRY_WARNING, msg, first_ts, kind))

            # Trend analysis only for channels with a configured rule.
            if suffix not in TREND_RULES or len(values) < MIN_POINTS_FOR_TREND:
                continue

            slope, intercept, r_squared, sigma = _linear_regression(values)
            n = len(values)
            fitted_first = intercept
            fitted_last = intercept + slope * (n - 1)
            # Use fitted endpoints, not raw first/last, so a single noisy sample
            # doesn't dominate the percent-change number.
            denominator = abs(fitted_first) or abs(fitted_last) or 1.0
            percent_change = (fitted_last - fitted_first) / denominator * 100.0
            last_timestamp = samples[-1][1]

            summary = (
                f"{channel_name}: "
                f"{_format_value(suffix, values[0])} -> {_format_value(suffix, values[-1])} "
                f"(fit: {percent_change:+.1f}% over {n} samples, "
                f"slope={_format_slope(suffix, slope)}, "
                f"R²={r_squared:.2f}, σ={_format_sigma(suffix, sigma)})"
            )
            self.trends.append(summary)

            if r_squared < MIN_TREND_R_SQUARED:
                continue
            direction, threshold, prefix = TREND_RULES[suffix]
            if (direction == "up" and percent_change >= threshold) or \
               (direction == "down" and percent_change <= -threshold):
                # Stable key (trend, channel, prefix). Once flagged, the
                # same condition won't re-fail the build; the trend line
                # is still printed so the values stay visible.
                self.alerts.append(Alert(
                    TELEMETRY_WARNING,
                    f"{prefix}: {summary}",
                    last_timestamp,
                    ("trend", channel_name, prefix),
                ))


def _print_summary(results: Results, suppressed: int,
                   soak_start: Optional[datetime]):
    total_samples = sum(len(samples) for samples in results.channels.values())
    print("")
    if soak_start is not None:
        print(f"Soak Started:             {soak_start.isoformat()}")
    print(f"Events Decoded:           {len(results.events)}")
    print(f"Channel Samples Decoded:  {total_samples}")
    print(f"Numeric Channels Tracked: {len(results.channels)}")
    print(f"Alerts (new):             {len(results.alerts)}")
    if suppressed:
        print(f"Alerts (already logged):  {suppressed}")

    if results.alerts:
        print("")
        print("ALERTS:")
        for alert in results.alerts:
            ts = f" [{alert.timestamp}]" if alert.timestamp else ""
            elapsed = _format_elapsed(alert.timestamp, soak_start)
            print(f" {alert.severity} - {alert.message}{ts}{elapsed}")

    if results.trends:
        print("")
        print("TREND ANALYSIS:")
        for trend in results.trends:
            print(f" {trend}")


# Header written by soak-setup; matches `# SOAK STARTED <ISO8601-no-tz>`.
_SOAK_HEADER_RE = re.compile(r"^# SOAK STARTED (.+)$")


def _load_seen_keys(path: Path) -> Tuple[Set[str], Optional[datetime]]:
    """Return (already-seen alert keys, soak start time) from the failure log.

    The header line is informational only - we strip it before keying so a
    line written by setup.sh never matches an alert key.
    """
    if not path.exists():
        return set(), None
    soak_start: Optional[datetime] = None
    keys: Set[str] = set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        header = _SOAK_HEADER_RE.match(line)
        if header:
            try:
                soak_start = datetime.fromisoformat(header.group(1).strip())
            except ValueError:
                pass  # malformed header - alerts still work, "since" just won't render
            continue
        keys.add(line.split("\t# ", 1)[0].rstrip())
    return keys, soak_start


def _format_elapsed(timestamp: str, soak_start: Optional[datetime]) -> str:
    """Render '(X weeks, Y days, Z hours, W minutes since soak start)' for a
    GDS-style ISO timestamp. Returns '' if either side can't be parsed -
    silently degrading is fine because the absolute timestamp is still shown.
    """
    if soak_start is None or not timestamp:
        return ""
    try:
        when = datetime.fromisoformat(timestamp)
    except ValueError:
        return ""
    delta = when - soak_start
    if delta.total_seconds() < 0:
        return ""
    total_minutes = int(delta.total_seconds() // 60)
    weeks, rem = divmod(total_minutes, 7 * 24 * 60)
    days, rem = divmod(rem, 24 * 60)
    hours, minutes = divmod(rem, 60)
    parts = []
    if weeks:
        parts.append(f"{weeks} week{'s' if weeks != 1 else ''}")
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return f" ({', '.join(parts)} since soak start)"


def _append_failure_log(path: Path, alerts: List[Alert],
                        soak_start: Optional[datetime]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for alert in alerts:
            elapsed = _format_elapsed(alert.timestamp, soak_start)
            # Comment trail keeps the file human-readable (key + message).
            fh.write(f"{alert.key_line()}\t# {alert.message}{elapsed}\n")


class SoakArgs(ParserBase):
    DESCRIPTION = "F´ Soak Test Monitor"
    def get_arguments(self):
        return {
            ("--com-logs",): {"type": Path, "default": None,
                "help": "Directory of Svc::ComLogger .com files (may be empty)."},
            ("--gds-logs",): {"type": Path, "default": None,
                "help": "Directory of GDS session logs containing channel.log/event.log "
                        "(e.g. <deployment>/logs). Preferred when both sources are given."},
            ("--failure-log",): {"type": Path, "default": None,
                "help": "Path to a cross-run failure log. Alerts whose stable key is "
                        "already present are suppressed from the current run; new alerts "
                        "are appended. Soak setup creates this file empty."},
        }
    def handle_arguments(self, args, **_):
        if args.com_logs is None and args.gds_logs is None:
            raise SystemExit("error: at least one of --com-logs or --gds-logs is required")
        return args


def main():
    args, _ = ParserBase.parse_args([StandardPipelineParser, SoakArgs])
    results = Results()

    # Prefer GDS text logs, use ComLogger otherwise
    gds_rows = process_gds_logs(args.gds_logs, results)
    if gds_rows == 0 and args.com_logs is not None:
        process_com_logs(args, args.com_logs, results)
    elif gds_rows > 0 and args.com_logs is not None:
        print("GDS logs supplied analysis, skipping ComLogger parsing.")

    results.analyze()

    suppressed = 0
    soak_start: Optional[datetime] = None
    if args.failure_log is not None:
        seen, soak_start = _load_seen_keys(args.failure_log)
        new_alerts = [alert for alert in results.alerts if alert.key_line() not in seen]
        suppressed = len(results.alerts) - len(new_alerts)
        results.alerts = new_alerts
        if new_alerts:
            _append_failure_log(args.failure_log, new_alerts, soak_start)

    _print_summary(results, suppressed, soak_start)
    sys.exit(1 if any(alert.severity in FAILING_SEVERITIES for alert in results.alerts) else 0)


if __name__ == "__main__":
    main()
