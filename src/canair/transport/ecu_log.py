"""Per-ECU response logging.

Tracks the active ECU from ATSH headers and appends UDS query responses to per-ECU, per-day
files under ``~/.config/canair/logs/ecu/``. AT commands are not logged. The central
diagnostics log is :mod:`canair.core.eventlog`.
"""

import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime

from canair.core import eventlog

_ecu_loggers: dict[str, logging.Logger] = {}  # ecu_name -> logger
_active_ecu: str | None = None  # current ECU name (from ATSH header)
_ecu_names: dict[int, str] = {}  # tx_id -> ECU name, given to init_logging
_date_str: str = ""
_initialized: bool = False


def ecu_log_dir():
    """Directory of the per-ECU response logs, beside the central event log.

    User state under the config directory: a path derived from the package location would be
    ``site-packages/logs`` in an installed copy, which a reinstall deletes.
    """
    return eventlog.event_log_path().parent / "ecu"


def _get_ecu_logger(ecu_name: str) -> logging.Logger:
    """Get or create a per-ECU logger."""
    if ecu_name in _ecu_loggers:
        return _ecu_loggers[ecu_name]

    log_dir = ecu_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"can_request.ecu.{ecu_name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        fh = logging.FileHandler(log_dir / f"{ecu_name}-{_date_str}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(fh)
    _ecu_loggers[ecu_name] = logger
    return logger


def init_logging(ecu_names: Mapping[int, str] | None = None):
    """Initialize logging: the date string for log filenames and the TX id -> ECU name map."""
    global _date_str, _initialized, _ecu_names
    _date_str = datetime.now().strftime("%Y-%m-%d")
    _ecu_names = dict(ecu_names or {})
    _initialized = True


def log_command(cmd: str):
    """Track ECU header changes from commands. No file output."""
    global _active_ecu
    m = re.match(r"ATSH([0-9A-Fa-f]{3})", cmd)
    if m:
        tx_id = int(m.group(1), 16)
        _active_ecu = _ecu_names.get(tx_id, f"0x{tx_id:03X}")


def log_response(cmd: str, response: str):
    """Log a UDS response to the active ECU's log file.

    AT commands and responses without an active ECU are silently skipped.
    """
    if not _initialized or not _active_ecu or cmd.startswith("AT"):
        return
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    resp_oneline = response.replace("\n", " | ")
    line = f"[{ts}] {cmd} -> {resp_oneline}"
    ecu_logger = _get_ecu_logger(_active_ecu)
    ecu_logger.info(line)
