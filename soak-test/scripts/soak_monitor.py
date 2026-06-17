#!/usr/bin/env python3
"""F´ Soak Test Monitor.

Uses gdslog/comlog_parser.py to parse logs, stores events and 
telemetry in a results object, and analyzes trends to identify potential issues
"""

import sys
from pathlib import Path
from typing import Dict, List, Tuple

from fprime_gds.executables.cli import ParserBase, StandardPipelineParser

from comlog_parser import process_com_logs
from gdslog_parser import process_gds_logs

MIN_POINTS_FOR_TREND = 5         # need a few samples before a trend is meaningful
LEAK_GROWTH_PERCENT = 10.0       # resource grew by this much across the window -> alert
DEPLETION_DROP_PERCENT = 50.0    # free resource dropped by this much -> alert
HIGH_CPU_PERCENT = 90.0

def _classify(name: str) -> str:
    """Classify F' channel name (case-insensitive) for trend/threshold rules."""
    n = name.lower()
    if "memory_used" in n: return "memory_usage"
    if "non_volatile_free" in n: return "free_storage"
    if "memory_total" in n or "non_volatile_total" in n: return "baseline"
    if n.endswith(".cpu") or ".cpu_" in n: return "cpu"
    if any(k in n for k in ("hibuffs", "lobuffs", "nobuffs", "buffermanager")): return "buffer_pool"
    if "queuedepth" in n: return "queue_depth"
    return "other"

def _to_float(val):
    if isinstance(val, bool): return None
    if isinstance(val, (int, float)): return float(val)
    try: return float(str(val).replace(",", ""))
    except (TypeError, ValueError): return None

class Results:
    """Accumulates decoded events/channels from both parsers, applies
    threshold rules at ingest time, and runs trend analysis at the end."""

    def __init__(self):
        self.values: Dict[str, List[float]] = {}        # channel -> samples
        self.last_ts: Dict[str, str] = {}               # channel -> last timestamp
        self.alerts: List[Tuple[str, str, str]] = []    # (severity, message, ts)
        self.trends: List[dict] = []
        self.events = 0
        self.channels = 0

    def alert(self, severity: str, msg: str, ts: str = ""):
        self.alerts.append((severity, msg, ts))

    # ---- ingest from comlog_parser (decoded GDS objects) ----

    def add_event(self, ev):
        self.events += 1
        sev = str(ev.template.severity)
        ts = str(getattr(ev, "time", ""))
        body = f"{ev.template.name}: {getattr(ev, 'args', '')}"
        self._record_event(sev, body, ts)

    def add_channel(self, ch):
        self.channels += 1
        value = _to_float(ch.get_val())
        if value is None:
            return
        name = ch.template.name
        ts = str(getattr(ch, "time", ""))
        self._record_channel(name, value, ts)

    # ---- ingest from gdslog_parser (already-stringified CSV rows) ----

    def add_text_event(self, name: str, severity: str, body: str, ts: str = ""):
        self.events += 1
        self._record_event(severity, f"{name}: {body}", ts)

    def add_text_channel(self, name: str, value: float, ts: str = ""):
        self.channels += 1
        self._record_channel(name, value, ts)

    # ---- shared internals ----

    def _record_event(self, sev: str, body: str, ts: str):
        if sev == "EventSeverity.FATAL":
            self.alert("FATAL", body, ts)
        elif sev == "EventSeverity.WARNING_HI":
            self.alert("WARNING_HI", body, ts)
        elif sev == "EventSeverity.WARNING_LO":
            self.alert("WARNING_LO", body, ts)

    def _record_channel(self, name: str, value: float, ts: str):
        self.values.setdefault(name, []).append(value)
        self.last_ts[name] = ts
        kind = _classify(name)
        if kind == "buffer_pool" and value == 0:
            self.alert("WARNING_HI", f"Buffer pool exhausted: {name} = 0", ts)
        elif kind == "cpu" and value > HIGH_CPU_PERCENT:
            self.alert("WARNING_LO", f"High CPU usage: {name} = {value:g}%", ts)

    def analyze_trends(self):
        for name, vals in self.values.items():
            if len(vals) < MIN_POINTS_FOR_TREND:
                continue
            first, last = vals[0], vals[-1]
            denom = abs(first) or abs(last) or 1.0
            pct = (last - first) / denom * 100.0
            self.trends.append({"name": name, "n": len(vals), "first": first,
                                "last": last, "min": min(vals), "max": max(vals), "pct": pct})
            kind = _classify(name)
            ts = self.last_ts[name]
            base = f"{name}: {first:g} -> {last:g} ({pct:+.1f}% over {len(vals)} samples)"
            if kind == "memory_usage" and pct >= LEAK_GROWTH_PERCENT:
                self.alert("WARNING_LO", f"Possible memory leak: {base}", ts)
            elif kind in ("free_storage", "buffer_pool") and pct <= -DEPLETION_DROP_PERCENT:
                self.alert("WARNING_HI", f"Possible resource depletion: {base}", ts)
            elif kind == "cpu" and pct >= LEAK_GROWTH_PERCENT:
                self.alert("WARNING_LO", f"Rising CPU trend: {base}", ts)
            elif kind == "queue_depth" and pct >= LEAK_GROWTH_PERCENT:
                self.alert("WARNING_HI", f"Rising queue depth: {base}", ts)


class SoakArgs(ParserBase):
    DESCRIPTION = "F´ Soak Test Monitor"
    def get_arguments(self):
        return {
            ("--com-logs",): {"type": Path, "required": False, "default": None,
                "help": "Directory of Svc::ComLogger .com files (may be empty)."},
            ("--gds-logs",): {"type": Path, "required": False, "default": None,
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
    # Prefer GDS text logs when both sources are provided; fall back to
    # decoding Svc::ComLogger .com files only if the GDS run produced nothing.
    gds_rows = process_gds_logs(args.gds_logs, results)
    if gds_rows == 0 and args.com_logs is not None:
        process_com_logs(args, args.com_logs, results)
    elif gds_rows > 0 and args.com_logs is not None:
        print("GDS logs supplied analysis; skipping ComLogger decode.")

    results.analyze_trends()

    print("")
    print(f"Events Decoded:           {results.events}")
    print(f"Channel Samples Decoded:  {results.channels}")
    print(f"Numeric Channels Tracked: {len(results.values)}")
    print(f"Alerts:                   {len(results.alerts)}")

    if results.alerts:
        print("\nALERTS:")
        for sev, msg, ts in results.alerts:
            print(f" {sev} - {msg}{f' [{ts}]' if ts else ''}")

    if results.trends:
        print("\nTREND ANALYSIS:")
        for t in sorted(results.trends, key=lambda x: abs(x["pct"]), reverse=True):
            arrow = "^" if t["pct"] > 0 else ("v" if t["pct"] < 0 else "-")
            print(f" {arrow} {t['name']}: {t['first']:g} -> {t['last']:g} "
                  f"({t['pct']:+.1f}% over {t['n']} samples, "
                  f"min={t['min']:g}, max={t['max']:g})")

    sys.exit(1 if any(a[0] == "FATAL" for a in results.alerts) else 0)


if __name__ == "__main__":
    main()
