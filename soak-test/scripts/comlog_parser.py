"""Decode Svc::ComLogger .com files via the fprime-gds pipeline.

`process_com_logs` is the only public entry point. It sets up a
StandardPipeline configured for ComLogger's framing (no key, U16 length),
registers consumer adapters that forward decoded events/channels to the
caller's Results object, then feeds each .com file's bytes through the
distributor manually.
"""

from pathlib import Path
from typing import Optional

from fprime_gds.common.handlers import DataHandler
from fprime_gds.common.models.serialize.numerical_types import U16Type
from fprime_gds.common.pipeline.standard import StandardPipeline
from fprime_gds.common.utils.config_manager import ConfigManager


class _Handler(DataHandler):
    """Adapter routing the GDS DataHandler callback to a plain function.
    Wraps each call in try/except so a malformed record never aborts the run."""
    def __init__(self, fn):
        self.fn = fn

    def data_callback(self, data, sender=None):
        try:
            self.fn(data)
        except Exception:
            pass


def _make_pipeline(args, config) -> StandardPipeline:
    """Stand up a StandardPipeline configured to decode ComLogger records.
    setup() launches transport/file-uplink threads; we feed data manually via
    distributor.on_recv(), so disconnect() afterwards lets the process exit."""
    p = StandardPipeline()
    p.transport_implementation = args.connection_transport
    try:
        p.setup(config=config, dictionaries=args.dictionaries,
                file_store=args.files_storage_directory,
                logging_prefix=args.logs, data_logging_enabled=False)
    finally:
        try:
            p.disconnect()
        except Exception:
            pass
    return p


def process_com_logs(args, com_logs: Optional[Path], results) -> int:
    """Decode every .com file under com_logs (and a sibling ComLoggerFiles/
    if present) and forward decoded events/channels to results.
    Returns the number of files processed."""
    if com_logs is None:
        return 0

    search_paths = [com_logs]
    gds_com_logs = com_logs.parent / "ComLoggerFiles"
    if gds_com_logs.exists() and gds_com_logs.is_dir():
        search_paths.append(gds_com_logs)

    files = []
    for path in search_paths:
        if path.exists():
            files.extend(sorted(path.glob("**/*.com")))

    if not files:
        print(f"No ComLogger .com files found in {search_paths}; skipping ComLogger analysis.")
        return 0

    # Svc::ComLogger frames each Fw::ComBuffer with no key and a U16 length
    config = ConfigManager()
    config.set_config("use_key", False)
    config.set_config("msg_len", U16Type)
    pipeline = _make_pipeline(args, config)

    print(f"Processing {len(files)} ComLogger .com file(s) from {len(search_paths)} location(s)")
    pipeline.coders.register_event_consumer(_Handler(results.add_event))
    pipeline.coders.register_channel_consumer(_Handler(results.add_channel))
    for f in files:
        try:
            data = f.read_bytes()
            if data:
                pipeline.distributor.on_recv(data)
        except Exception as exc:
            print(f"Error processing {f}: {exc}")
    return len(files)
