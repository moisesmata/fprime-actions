#!/usr/bin/env python3
"""
F´ Soak Test Monitor
====================

Analyzes data captured during a soak test for health, resource, and stability
problems. 

Inputs (all optional unless noted):
  * ``--dictionary`` (required by StandardPipelineParser): the deployment
    dictionary, used to decode events and channels.
  * ``--com-logs``  : a directory of ``Svc::ComLogger`` ``.com`` files. These are
    read **if present**; if the directory is missing or empty the monitor simply
    reports that there was nothing to analyze and exits successfully.

What it does:
  1. Decodes every event and channel found in the ``.com`` files.
  2. Raises alerts for FATAL/WARNING_HI events and for instantaneous resource
     thresholds (buffer exhaustion, high CPU/memory).
  3. Runs trend analysis over every numeric channel time-series to catch slow
     degradations that a single snapshot would miss (e.g. a memory leak or a
     steadily draining buffer pool).

Exit code is non-zero only when a FATAL condition is detected.
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fprime_gds.common.handlers import DataHandler
from fprime_gds.common.models.serialize.numerical_types import U16Type
from fprime_gds.common.pipeline.standard import StandardPipeline
from fprime_gds.common.utils.config_manager import ConfigManager
from fprime_gds.executables.cli import ParserBase, StandardPipelineParser

# Keywords used to classify numeric channels. Matching is case-insensitive and substring based.
MEMORY_KEYWORDS = ("memory", "heap", "ram", "mem")
CPU_KEYWORDS = ("cpu", "load")
BUFFER_FREE_KEYWORDS = ("buffer", "buff", "free", "empty", "available", "avail", "remaining")

# Thresholds for instantaneous (single-sample) alerts.
HIGH_UTILIZATION_PERCENT = 90.0

# Trend-analysis tuning.
MIN_POINTS_FOR_TREND = 5            # need a few samples before a trend is meaningful
LEAK_GROWTH_PERCENT = 10.0          # resource grew by this much across the window -> alert
DEPLETION_DROP_PERCENT = 50.0       # free resource dropped by this much -> alert


class SoakAnalysisResults:
    """Container for soak test analysis results."""

    def __init__(self):
        # name -> list of {"timestamp": str, "value": float}
        self.metrics: Dict[str, List[Dict[str, Any]]] = {}
        self.alerts: List[Dict[str, str]] = []
        self.trends: List[Dict[str, Any]] = []
        self.event_count = 0
        self.channel_count = 0
        self.timestamp = datetime.now().isoformat()

    def add_alert(self, message, severity, timestamp=""):
        """Add an alert.

        Args:
            message: Alert message with all details.
            severity: 'FATAL' or 'WARNING'.
            timestamp: When the alert occurred.
        """
        self.alerts.append(
            {"message": message, "severity": severity, "timestamp": timestamp}
        )

    def record_metric(self, name, value, timestamp=""):
        """Record one numeric sample for a channel time-series."""
        self.metrics.setdefault(name, []).append(
            {"timestamp": timestamp, "value": value}
        )

    def analyze_trends(self):
        """Analyze trends over time for every collected numeric channel.

        For each channel with enough samples we fit a least-squares line over the
        sample index and look at the relative change from the first to the last
        sample. We then classify the channel by name so we can tell the
        difference between "a number that went up" and "a problem":

          * Memory/heap usage that climbs steadily -> possible leak (WARNING).
          * A free-buffer / free-memory pool that drains steadily -> possible
            exhaustion (WARNING).
          * CPU/load that climbs steadily -> rising load (WARNING).

        Findings are stored on ``self.trends`` for the summary and concerning
        ones additionally raise alerts.
        """
        for name, readings in self.metrics.items():
            if len(readings) < MIN_POINTS_FOR_TREND:
                continue

            values = [r["value"] for r in readings]
            slope = _least_squares_slope(values)
            first, last = values[0], values[-1]
            lowest, highest = min(values), max(values)
            pct_change = _percent_change(first, last)

            finding = {
                "name": name,
                "samples": len(values),
                "first": first,
                "last": last,
                "min": lowest,
                "max": highest,
                "slope": slope,
                "pct_change": pct_change,
            }
            self.trends.append(finding)

            lname = name.lower()
            is_memory = any(k in lname for k in MEMORY_KEYWORDS)
            is_cpu = any(k in lname for k in CPU_KEYWORDS)
            is_free_pool = any(k in lname for k in BUFFER_FREE_KEYWORDS)
            last_ts = readings[-1]["timestamp"]

            # Steadily rising memory usage is the classic soak-test leak signature.
            if is_memory and slope > 0 and pct_change >= LEAK_GROWTH_PERCENT:
                self.add_alert(
                    f"Possible resource leak: {name} rose {pct_change:.1f}% over "
                    f"{len(values)} samples ({first:g} -> {last:g})",
                    "WARNING",
                    last_ts,
                )
            # A free pool that keeps draining will eventually exhaust.
            elif is_free_pool and slope < 0 and pct_change <= -DEPLETION_DROP_PERCENT:
                self.add_alert(
                    f"Possible resource depletion: {name} fell {abs(pct_change):.1f}% "
                    f"over {len(values)} samples ({first:g} -> {last:g})",
                    "WARNING",
                    last_ts,
                )
            # Rising CPU/load over a long soak is worth surfacing.
            elif is_cpu and slope > 0 and pct_change >= LEAK_GROWTH_PERCENT:
                self.add_alert(
                    f"Rising load trend: {name} climbed {pct_change:.1f}% over "
                    f"{len(values)} samples ({first:g} -> {last:g})",
                    "WARNING",
                    last_ts,
                )


def _least_squares_slope(values: List[float]) -> float:
    """Best-fit slope of ``values`` against their index (0..n-1).

    Returns the per-sample change. No external dependencies (no numpy) so the
    monitor stays light and runs anywhere fprime-gds is installed.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = 0.0
    den = 0.0
    for i, y in enumerate(values):
        dx = i - mean_x
        num += dx * (y - mean_y)
        den += dx * dx
    if den == 0:
        return 0.0
    return num / den


def _percent_change(first: float, last: float) -> float:
    """Relative change from first to last as a percentage.

    Falls back to an absolute-based denominator when the baseline is zero so we
    still get a meaningful, finite number.
    """
    denom = abs(first) if first != 0 else (abs(last) if last != 0 else 1.0)
    return (last - first) / denom * 100.0


class EventCollector(DataHandler):
    """Event consumer that inherits from DataHandler."""

    def __init__(self, results):
        self.results = results

    def data_callback(self, event_data, sender=None):
        """Handle decoded event data."""
        try:
            self.results.event_count += 1
            event_name = (
                event_data.template.name
                if hasattr(event_data, "template")
                else str(event_data)
            )
            severity = (
                event_data.template.severity
                if hasattr(event_data, "template")
                else "UNKNOWN"
            )
            description = str(event_data.args) if hasattr(event_data, "args") else ""
            timestamp = str(event_data.time) if hasattr(event_data, "time") else ""

            # Map FATAL events to FATAL alerts and WARNING_HI to WARNING alerts.
            severity_str = str(severity)
            if severity_str == "EventSeverity.FATAL":
                self.results.add_alert(
                    f"{event_name}: {description}", "FATAL", timestamp
                )
            elif severity_str == "EventSeverity.WARNING_HI":
                self.results.add_alert(
                    f"{event_name}: {description}", "WARNING", timestamp
                )
        except Exception:
            # Silently ignore parsing errors to be robust against odd records.
            pass


class ChannelCollector(DataHandler):
    """Channel consumer that inherits from DataHandler."""

    def __init__(self, results):
        self.results = results

    def data_callback(self, channel_data, sender=None):
        """Handle decoded channel data."""
        try:
            self.results.channel_count += 1
            ch_name = (
                channel_data.template.name
                if hasattr(channel_data, "template")
                else str(channel_data)
            )
            ch_val = channel_data.val if hasattr(channel_data, "val") else None
            timestamp = str(channel_data.time) if hasattr(channel_data, "time") else ""

            value = _coerce_numeric(ch_val)
            if value is None:
                # Non-numeric channel (string/enum/struct): nothing to trend.
                return

            # Record every numeric channel so trend analysis is deployment-agnostic.
            self.results.record_metric(ch_name, value, timestamp)

            # Instantaneous, name-based threshold alerts.
            lname = ch_name.lower()
            is_free_pool = any(k in lname for k in BUFFER_FREE_KEYWORDS)
            is_cpu = any(k in lname for k in CPU_KEYWORDS)
            is_memory = any(k in lname for k in MEMORY_KEYWORDS)

            if is_free_pool and value == 0:
                self.results.add_alert(
                    f"Buffer/resource exhaustion detected: {ch_name} = {value:g}",
                    "WARNING",
                    timestamp,
                )
            elif is_cpu and value > HIGH_UTILIZATION_PERCENT:
                self.results.add_alert(
                    f"High CPU usage detected: {ch_name} = {value:g}%",
                    "WARNING",
                    timestamp,
                )
            elif is_memory and value > HIGH_UTILIZATION_PERCENT:
                self.results.add_alert(
                    f"High memory usage detected: {ch_name} = {value:g}%",
                    "WARNING",
                    timestamp,
                )
        except Exception:
            # Silently ignore parsing errors to be robust against odd records.
            pass


def _coerce_numeric(value):
    """Best-effort conversion of a channel value to float, or None if not numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def process_logs(pipeline, logs_path, results):
    """Decode every ComLogger ``.com`` file under ``logs_path`` into ``results``.

    Reading is optional: a missing directory or zero files is reported and
    treated as "nothing to analyze" rather than an error.
    """
    if logs_path is None:
        print("No --com-logs directory provided; skipping ComLogger analysis.")
        return

    if not logs_path.exists() or not logs_path.is_dir():
        print(f"ComLogger directory not found ({logs_path}); skipping analysis.")
        return

    log_files = sorted(logs_path.glob("**/*.com"))
    print(f"Processing {len(log_files)} ComLogger .com file(s) from {logs_path}...")
    if not log_files:
        print("No ComLogger .com files found; nothing to analyze.")
        return

    event_consumer = EventCollector(results)
    channel_consumer = ChannelCollector(results)
    pipeline.coders.register_event_consumer(event_consumer)
    pipeline.coders.register_channel_consumer(channel_consumer)

    for log_file in log_files:
        try:
            with open(log_file, "rb") as file_handle:
                data = file_handle.read()
            if data:
                pipeline.distributor.on_recv(data)
        except Exception as exc:
            print(f"Error processing {log_file}: {exc}")


class SoakMonitorArgumentParser(ParserBase):
    """Parser for F´ Soak Monitor additional arguments."""

    DESCRIPTION = (
        "F´ Soak Test Monitor - analyzes captured telemetry/events for FATALs, "
        "resource thresholds, and degradation trends."
    )

    def get_arguments(self) -> Dict[Tuple[str, ...], Dict[str, Any]]:
        """Arguments for soak monitoring."""
        return {
            ("--com-logs",): {
                "action": "store",
                "required": False,
                "default": None,
                "type": Path,
                "help": (
                    "Optional path to a directory of Svc::ComLogger .com files. "
                    "Read if present; ignored (not an error) if missing or empty."
                ),
            }
        }

    def handle_arguments(self, args, **kwargs):
        """Handle arguments as parsed.

        ComLogger reading is optional, so a missing directory is downgraded to a
        warning and the path is cleared rather than raising.
        """
        if args.com_logs is not None:
            if not args.com_logs.exists() or not args.com_logs.is_dir():
                print(
                    f"WARNING: --com-logs path does not exist or is not a "
                    f"directory: {args.com_logs}. Continuing without ComLogger data."
                )
                args.com_logs = None
        return args


def pipeline_factory(args_ns, config) -> StandardPipeline:
    """A factory of the standard pipeline given the handled arguments."""
    # NOTE (fprime_gds 4.x): StandardPipeline.setup() takes "dictionaries"
    # (a loaded Dictionaries object, available as args_ns.dictionaries) instead
    # of a "dictionary" path, and no longer accepts packet_spec/packet_set_name.
    pipeline_arguments = {
        "config": config,
        "dictionaries": args_ns.dictionaries,
        "file_store": args_ns.files_storage_directory,
        "logging_prefix": args_ns.logs,
        # We feed ComLogger data in manually, so disable GDS data logging.
        "data_logging_enabled": False,
    }
    pipeline = StandardPipeline()
    pipeline.transport_implementation = args_ns.connection_transport
    try:
        pipeline.setup(**pipeline_arguments)
        # Tear down the transport/file-uplink background threads started by
        # setup(). We never connect; ComLogger data is fed in manually via
        # pipeline.distributor.on_recv() (synchronous), and disconnecting here
        # lets the process exit cleanly once analysis completes.
        pipeline.disconnect()
    except Exception:
        try:
            pipeline.disconnect()
        finally:
            raise
    return pipeline


def main():
    args, _ = ParserBase.parse_args([StandardPipelineParser, SoakMonitorArgumentParser])
    config = ConfigManager()
    # Configure the distributor to parse Svc::ComLogger .com records. ComLogger
    # stores each Fw::ComBuffer with no key and a U16 length prefix (see
    # Svc/ComLogger/ComLogger.cpp), so override the GDS defaults accordingly.
    config.set_config("use_key", False)
    config.set_config("msg_len", U16Type)
    pipeline = pipeline_factory(args, config)

    results = SoakAnalysisResults()

    print("=" * 50)
    print("F´ SOAK TEST MONITOR")
    print("=" * 50)

    process_logs(pipeline, args.com_logs, results)
    results.analyze_trends()

    return_code = 0
    print("\nMONITORING RESULTS:")
    print("-" * 50)
    print(f"Events Decoded:           {results.event_count}")
    print(f"Channel Samples Decoded:  {results.channel_count}")
    print(f"Numeric Channels Tracked: {len(results.metrics)}")
    print(f"Alerts Generated:         {len(results.alerts)}")

    if any(alert["severity"] == "FATAL" for alert in results.alerts):
        return_code = 1

    if results.alerts:
        print("\nALERTS:")
        for alert in results.alerts:
            ts = f" [{alert['timestamp']}]" if alert["timestamp"] else ""
            print(f"{alert['severity']} - {alert['message']}{ts}")

    if results.trends:
        print("\nTREND ANALYSIS:")
        for t in sorted(results.trends, key=lambda x: abs(x["pct_change"]), reverse=True):
            direction = "↑" if t["slope"] > 0 else ("↓" if t["slope"] < 0 else "→")
            print(
                f" {direction} {t['name']}: {t['first']:g} -> {t['last']:g} "
                f"({t['pct_change']:+.1f}% over {t['samples']} samples, "
                f"min={t['min']:g}, max={t['max']:g})"
            )

    print("=" * 50)
    if return_code == 0:
        print("MONITORING COMPLETED SUCCESSFULLY")
    else:
        print("MONITORING DETECTED FATAL CONDITION(S)")
    sys.exit(return_code)


if __name__ == "__main__":
    main()
