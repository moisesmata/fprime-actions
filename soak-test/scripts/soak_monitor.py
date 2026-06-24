#!/usr/bin/env python3
"""F´ Soak Test Monitor.

Uses gdslog/comlog_parser.py to parse logs, stores raw events and telemetry
in a Results struct, and during analyze() walks them and emits alerts.

Channels we analyze:
  SystemResources: CPU + CPU_NN (any sample over HIGH_CPU_PERCENT alerts),
                   MEMORY_USED (leak trend), NON_VOLATILE_FREE (depletion trend)
  BufferManager:   CurrBuffs, HiBuffs (rising = buffer leak trend),
                   NoBuffs, EmptyBuffs (any > 0 = allocation failure)

Trend analysis runs a least-squares linear regression against sample index,
reporting slope, R², and residual σ. Alerts only fire when the fit is
trustworthy (R² ≥ MIN_TREND_R_SQUARED), so a flat-but-noisy series doesn't
trip a leak alert. To keep passing-run output readable, only channels with
a configured trend rule (see TREND_RULES) appear in the trend section —
per-core CPU, constants like NON_VOLATILE_TOTAL, and other numeric
channels are alerted on (where applicable) but not printed as trend lines.
"""

import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from fprime_gds.executables.cli import ParserBase, StandardPipelineParser
from comlog_parser import process_com_logs
from gdslog_parser import process_gds_logs

MIN_POINTS_FOR_TREND = 10        # need a few samples before a trend is meaningful
LEAK_GROWTH_PERCENT = 10.0       # resource grew by this much across the window -> alert
DEPLETION_DROP_PERCENT = 50.0    # free resource dropped by this much -> alert
MIN_TREND_R_SQUARED = 0.5        # below this the slope is noise, not a trend
HIGH_CPU_PERCENT = 95.0          # hard ceiling on CPU and CPU_NN samples

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

MEMORY_SUFFIXES = ("MEMORY_USED", "NON_VOLATILE_TOTAL", "NON_VOLATILE_FREE")
BYTES_PER_MB = 1024 * 1024


def _format_value(suffix: str, value: float) -> str:
    if suffix in MEMORY_SUFFIXES:
        return f"{value / BYTES_PER_MB:.2f} MB"
    return f"{value:g}"


def _format_slope(suffix: str, slope: float) -> str:
    if suffix in MEMORY_SUFFIXES:
        return f"{slope / BYTES_PER_MB:+.4g} MB/sample"
    return f"{slope:+.4g}/sample"


def _format_sigma(suffix: str, sigma: float) -> str:
    if suffix in MEMORY_SUFFIXES:
        return f"{sigma / BYTES_PER_MB:.2f} MB"
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


class Results:
    """Raw store of decoded events and telemetry samples. analyze() fills in
    self.alerts and self.trends from self.events and self.channels"""

    def __init__(self):
        self.channels: Dict[str, List[Tuple[float, str]]] = {}      # name -> [(value, ts)]
        self.events: List[Tuple[str, str, str, str]] = []           # [(severity, name, body, ts)]
        self.alerts: List[Tuple[str, str, str]] = []                # [(severity, msg, ts)]
        self.trends: List[str] = []                                 # one summary line per analyzed series

    def add_event(self, name: str, severity: str, body: str, ts: str = ""):
        self.events.append((severity.removeprefix("EventSeverity."), name, body, ts))

    def add_channel(self, name: str, value: float, ts: str = ""):
        self.channels.setdefault(name, []).append((value, ts))

    def analyze(self):
        # FSW events keep their native severity.
        for severity, event_name, body, timestamp in self.events:
            if severity in FSW_ALERT_SEVERITIES:
                self.alerts.append((severity, f"{event_name}: {body}", timestamp))

        for channel_name, samples in self.channels.items():
            suffix = channel_name.rsplit(".", 1)[-1]
            # SystemResources exposes CPU (average) and CPU_00, CPU_01, ...
            # per-core. We alert on both at the same threshold but only print
            # a trend line for channels in TREND_RULES (not per-core CPU).
            is_cpu = suffix == "CPU" or suffix.startswith("CPU_")
            values = [value for value, _ in samples]

            # Per-sample threshold checks.
            for value, timestamp in samples:
                if is_cpu and value > HIGH_CPU_PERCENT:
                    self.alerts.append((TELEMETRY_WARNING,
                        f"High CPU usage: {channel_name} = {value:g}%", timestamp))
                elif suffix == "NoBuffs" and value > 0:
                    self.alerts.append((TELEMETRY_WARNING,
                        f"Buffer allocation failure: {channel_name} = {value:g}", timestamp))
                elif suffix == "EmptyBuffs" and value > 0:
                    self.alerts.append((TELEMETRY_WARNING,
                        f"Empty buffer returned: {channel_name} = {value:g}", timestamp))

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
                self.alerts.append((TELEMETRY_WARNING,
                    f"{prefix}: {summary}", last_timestamp))


def _print_summary(results: Results):
    total_samples = sum(len(samples) for samples in results.channels.values())
    print("")
    print(f"Events Decoded:           {len(results.events)}")
    print(f"Channel Samples Decoded:  {total_samples}")
    print(f"Numeric Channels Tracked: {len(results.channels)}")
    print(f"Alerts:                   {len(results.alerts)}")

    if results.alerts:
        print("")
        print("ALERTS:")
        for severity, message, timestamp in results.alerts:
            print(f" {severity} - {message}" + (f" [{timestamp}]" if timestamp else ""))

    if results.trends:
        print("")
        print("TREND ANALYSIS:")
        for trend in results.trends:
            print(f" {trend}")

class SoakArgs(ParserBase):
    DESCRIPTION = "F´ Soak Test Monitor"
    def get_arguments(self):
        return {
            ("--com-logs",): {"type": Path, "default": None,
                "help": "Directory of Svc::ComLogger .com files (may be empty)."},
            ("--gds-logs",): {"type": Path, "default": None,
                "help": "Directory of GDS session logs containing channel.log/event.log "
                        "(e.g. <deployment>/logs). Preferred when both sources are given."},
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
    _print_summary(results)
    sys.exit(1 if any(alert[0] in FAILING_SEVERITIES for alert in results.alerts) else 0)

if __name__ == "__main__":
    main()
