#!/usr/bin/env python3
"""F´ Soak Test Monitor.

Uses gdslog/comlog_parser.py to parse logs, stores raw events and telemetry
in a Results struct, and during analyze() walks them and emits alerts.

Channels we analyze:
  SystemResources: MEMORY_USED (leak), NON_VOLATILE_FREE (depletion),
                   CPU + CPU_NN (high / rising)
  BufferManager:   CurrBuffs, HiBuffs (rising = buffer leak),
                   NoBuffs, EmptyBuffs (any > 0 = allocation failure)
"""

import sys
from pathlib import Path
from typing import Dict, List, Tuple

from fprime_gds.executables.cli import ParserBase, StandardPipelineParser

from comlog_parser import process_com_logs
from gdslog_parser import process_gds_logs

MIN_POINTS_FOR_TREND = 10         # need a few samples before a trend is meaningful
LEAK_GROWTH_PERCENT = 10.0       # resource grew by this much across the window -> alert
DEPLETION_DROP_PERCENT = 50.0    # free resource dropped by this much -> alert
HIGH_CPU_PERCENT = 90.0
ALERT_SEVERITIES = ("FATAL", "WARNING_HI", "WARNING_LO")

class Results:
    """Raw store of decoded events and telemetry samples. analyze() fills in
    self.alerts and self.trends from self.events and self.channels"""

    def __init__(self):
        self.channels: Dict[str, List[Tuple[float, str]]] = {}      # name -> [(value, ts)]
        self.events: List[Tuple[str, str, str, str]] = []           # [(severity, name, body, ts)]
        self.alerts: List[Tuple[str, str, str]] = []                # [(severity, msg, ts)]
        self.trends: List[str] = []                                # one summary line per analyzed series

    def add_event(self, name: str, severity: str, body: str, ts: str = ""):
        self.events.append((severity.removeprefix("EventSeverity."), name, body, ts))

    def add_channel(self, name: str, value: float, ts: str = ""):
        self.channels.setdefault(name, []).append((value, ts))

    def analyze(self):
        for severity, event_name, body, timestamp in self.events:
            if severity in ALERT_SEVERITIES:
                self.alerts.append((severity, f"{event_name}: {body}", timestamp))

        for channel_name, samples in self.channels.items():
            suffix = channel_name.rsplit(".", 1)[-1]
            is_cpu = suffix.startswith("CPU")
            values = [value for value, ts in samples]

            # Per-sample threshold checks
            for value, timestamp in samples:
                if is_cpu and value > HIGH_CPU_PERCENT:
                    self.alerts.append(("WARNING_LO",
                        f"High CPU usage: {channel_name} = {value:g}%", timestamp))
                elif suffix == "NoBuffs" and value > 0:
                    self.alerts.append(("WARNING_HI",
                        f"Buffer allocation failure: {channel_name} = {value:g}", timestamp))
                elif suffix == "EmptyBuffs" and value > 0:
                    self.alerts.append(("WARNING_HI",
                        f"Empty buffer returned: {channel_name} = {value:g}", timestamp))

            # Per-series trend check
            if len(values) < MIN_POINTS_FOR_TREND:
                continue
            first, last = values[0], values[-1]
            percent_change = (last - first) / (abs(first) or abs(last) or 1.0) * 100.0
            last_timestamp = samples[-1][1]
            summary = (f"{channel_name}: {first:g} -> {last:g} "
                       f"({percent_change:+.1f}% over {len(values)} samples)")
            self.trends.append(summary)

            if suffix == "MEMORY_USED" and percent_change >= LEAK_GROWTH_PERCENT:
                self.alerts.append(("WARNING_LO", f"Possible memory leak: {summary}", last_timestamp))
            elif suffix == "NON_VOLATILE_FREE" and percent_change <= -DEPLETION_DROP_PERCENT:
                self.alerts.append(("WARNING_HI", f"Possible storage depletion: {summary}", last_timestamp))
            elif is_cpu and percent_change >= LEAK_GROWTH_PERCENT:
                self.alerts.append(("WARNING_LO", f"Rising CPU trend: {summary}", last_timestamp))
            elif suffix in ("CurrBuffs", "HiBuffs") and percent_change >= LEAK_GROWTH_PERCENT:
                self.alerts.append(("WARNING_HI", f"Possible buffer leak: {summary}", last_timestamp))

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
    sys.exit(1 if any(alert[0] == "FATAL" for alert in results.alerts) else 0)

if __name__ == "__main__":
    main()
