"""Decode Svc::ComLogger .com files via the fprime-gds pipeline.

Stands up a StandardPipeline configured for ComLogger framing (no key, U16
length), registers consumer adapters that forward decoded events/channels
to the caller's Results, then feeds each .com file's bytes through the
distributor manually.
"""

from pathlib import Path
from typing import Optional

from fprime_gds.common.handlers import DataHandler
from fprime_gds.common.models.serialize.numerical_types import U16Type
from fprime_gds.common.pipeline.standard import StandardPipeline
from fprime_gds.common.utils.config_manager import ConfigManager

class _Handler(DataHandler):
    """Routes the GDS DataHandler callback to a plain function, swallowing
    per-record errors so a malformed record never aborts the run."""
    def __init__(self, callback):
        self.callback = callback
    def data_callback(self, item, sender=None):
        try: self.callback(item)
        except Exception: pass

def _ingest_event(results, event):
    results.add_event(event.template.name, str(event.template.severity),
                      str(getattr(event, "args", "")), str(getattr(event, "time", "")))

def _ingest_channel(results, channel):
    raw_value = channel.get_val()
    if isinstance(raw_value, bool):
        return
    try:
        numeric_value = (float(raw_value) if isinstance(raw_value, (int, float))
                         else float(str(raw_value).replace(",", "")))
    except (TypeError, ValueError):
        return
    results.add_channel(channel.template.name, numeric_value, str(getattr(channel, "time", "")))

def process_com_logs(args, com_logs: Optional[Path], results) -> int:
    """Decode every .com file under com_logs (and a sibling ComLoggerFiles/
    if present) into results. Returns the number of files processed."""
    if com_logs is None:
        return 0
    search_paths = [com_logs]
    sibling = com_logs.parent / "ComLoggerFiles"
    if sibling.is_dir():
        search_paths.append(sibling)
    files = sorted(com_file for path in search_paths if path.exists()
                   for com_file in path.glob("**/*.com"))
    if not files:
        print(f"No ComLogger .com files found in {search_paths}; skipping ComLogger analysis.")
        return 0

    # Svc::ComLogger frames each Fw::ComBuffer with no key and a U16 length.
    config = ConfigManager()
    config.set_config("use_key", False)
    config.set_config("msg_len", U16Type)
    pipeline = StandardPipeline()
    pipeline.transport_implementation = args.connection_transport
    try:
        pipeline.setup(config=config, dictionaries=args.dictionaries,
                       file_store=args.files_storage_directory,
                       logging_prefix=args.logs, data_logging_enabled=False)
    finally:
        try: pipeline.disconnect()
        except Exception: pass

    print(f"Processing {len(files)} ComLogger .com file(s) from {len(search_paths)} location(s)")
    pipeline.coders.register_event_consumer(_Handler(lambda event: _ingest_event(results, event)))
    pipeline.coders.register_channel_consumer(_Handler(lambda channel: _ingest_channel(results, channel)))
    for com_file in files:
        try:
            data = com_file.read_bytes()
            if data:
                pipeline.distributor.on_recv(data)
        except Exception as exc:
            print(f"Error processing {com_file}: {exc}")
    return len(files)
