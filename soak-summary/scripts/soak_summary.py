#!/usr/bin/env python3
"""Pretty-print the persistent soak log (written by soak-test/soak_monitor.py).

soak.log rows (tab-separated, append-only). Same format used by soak_monitor.py:

  # SOAK STARTED <iso>                       header line (written by soak-setup)
  E\\t<iso>\\t<severity>\\t<name>\\t<body>   FSW event we alerted on
  A\\t<iso>\\t<severity>\\t<message>         monitor-derived alert (threshold or trend)
  T\\t<iso>\\t<channel>\\t<value>            raw trend-channel sample (ignored here)
"""

import argparse
from datetime import datetime
from pathlib import Path


def parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def format_elapsed(when, start):
    """'(1 month, 2 weeks, 3 days, 4 hours, 5 minutes since soak start)' or ''.

    Negative deltas (event predates the recorded soak start — e.g. boot-time
    warnings flushed before the header line) render as '... before soak start'.
    """
    if when is None or start is None:
        return ""
    delta = when - start
    suffix = "since soak start" if delta.total_seconds() >= 0 else "before soak start"
    mins = int(abs(delta.total_seconds()) // 60)
    mo, mins = divmod(mins, 4 * 7 * 24 * 60)
    w, mins = divmod(mins, 7 * 24 * 60)
    d, mins = divmod(mins, 24 * 60)
    h, m = divmod(mins, 60)
    parts = [f"{n} {label}{'s' if n != 1 else ''}"
             for n, label in ((mo, "month"), (w, "week"), (d, "day"), (h, "hour")) if n]
    parts.append(f"{m} minute{'s' if m != 1 else ''}")
    return f"({', '.join(parts)} {suffix})"


def format_duration(start, latest):
    if not (start and latest):
        return ""
    secs = int((latest - start).total_seconds())
    d, secs = divmod(secs, 86400)
    h, secs = divmod(secs, 3600)
    m, _ = divmod(secs, 60)
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--soak-log", type=Path, required=True)
    args = parser.parse_args()
    if not args.soak_log.exists():
        raise SystemExit(f"error: soak log not found: {args.soak_log}")

    start = None
    entries = []       # (when, raw_ts, formatted_line)  - both E and A rows, sorted together in timeline
    telemetry = {}     # channel -> [(when, value, raw_ts), ...]
    malformed = 0

    for line in args.soak_log.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line:
            continue
        if line.startswith("# SOAK STARTED "):
            when = parse_iso(line[len("# SOAK STARTED "):].strip())
            if when and (start is None or when < start):
                start = when
            continue
        parts = line.split("\t")
        tag, ts = (parts[0], parts[1]) if len(parts) > 1 else ("", "")
        when = parse_iso(ts)
        if tag == "E" and len(parts) >= 5:
            entries.append((when, ts, f"[EVENT {parts[2]}] {parts[3]}: {parts[4]}"))
        elif tag == "A" and len(parts) >= 4:
            entries.append((when, ts, f"[ALERT {parts[2]}] {parts[3]}"))
        elif tag == "T" and len(parts) >= 4:
            try:
                telemetry.setdefault(parts[2], []).append((when, float(parts[3]), ts))
            except ValueError:
                malformed += 1
        else:
            malformed += 1

    total_t = sum(len(rows) for rows in telemetry.values())
    all_when = [start] + [e[0] for e in entries]
    for rows in telemetry.values():
        all_when.extend(r[0] for r in rows)
    latest = max((t for t in all_when if t), default=None)

    fmt = lambda dt: dt.strftime('%A %B %d %Y %I:%M:%S %p') if dt else "<unknown>"
    bar, dash = "=" * 72, "-" * 72

    print(f"{bar}\n F´ SOAK SUMMARY\n{bar}")
    print(f" Soak started:  {fmt(start)}")
    print(f" Latest entry:  {fmt(latest)}")
    print(f" Summary run:   {fmt(datetime.now())}")
    dur = format_duration(start, latest)
    if dur:
        print(f" Soak duration: {dur}")

    e_count = sum(1 for _, _, line in entries if line.startswith("[EVENT"))
    a_count = len(entries) - e_count
    ch_word = "channel" if len(telemetry) == 1 else "channels"
    print(f"\n FSW events (E rows):      {e_count}")
    print(f" Monitor alerts (A rows):  {a_count}")
    print(f" Telemetry rows (T rows):  {total_t} across {len(telemetry)} {ch_word}")
    if malformed:
        print(f" Malformed lines skipped: {malformed}")

    print(f"\n{dash}\n TIMELINE (Events + Alerts)\n{dash}")
    parsed = sorted((e for e in entries if e[0]), key=lambda e: e[0])
    unparsed = [e for e in entries if not e[0]]
    if not parsed and not unparsed:
        print(" (nothing recorded)")
    for when, ts, line in parsed:
        print(f" {ts}  {line}")
        elapsed = format_elapsed(when, start)
        if elapsed:
            print(f"   {elapsed}")
    if unparsed:
        print("\n (entries with unparseable timestamps)")
        for _, ts, line in unparsed:
            print(f" {ts or '<no timestamp>'}  {line}")


if __name__ == "__main__":
    main()
