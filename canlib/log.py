"""Per-ECU response logging to date-stamped files.

Tracks the active ECU from ATSH header commands and logs UDS query
responses to per-ECU files. AT commands are not logged.

Directory structure:
    logs/ecu/
        BMS-YYYY-MM-DD.log
        IGPM-YYYY-MM-DD.log
        ...
"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from .constants import SCRIPT_DIR

LOG_DIR = SCRIPT_DIR / "logs"
ECU_LOG_DIR = LOG_DIR / "ecu"

_ecu_loggers: dict[str, logging.Logger] = {}  # ecu_name -> logger
_active_ecu: str | None = None  # current ECU name (from ATSH header)
_ecu_lookup: dict[int, str] | None = None  # tx_id -> name cache
_date_str: str = ""
_initialized: bool = False


def _load_ecu_lookup() -> dict[int, str]:
    """Lazy-load ECU TX ID → name mapping from the ECU registry."""
    global _ecu_lookup
    if _ecu_lookup is not None:
        return _ecu_lookup
    try:
        from .ecus import load_ecus
        ecus = load_ecus()
        _ecu_lookup = {tx_id: info["name"] for tx_id, info in ecus.items()}
    except Exception:
        _ecu_lookup = {}
    return _ecu_lookup


def _get_ecu_logger(ecu_name: str) -> logging.Logger:
    """Get or create a per-ECU logger."""
    if ecu_name in _ecu_loggers:
        return _ecu_loggers[ecu_name]

    ECU_LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"can_request.ecu.{ecu_name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        fh = logging.FileHandler(
            ECU_LOG_DIR / f"{ecu_name}-{_date_str}.log", encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(fh)
    _ecu_loggers[ecu_name] = logger
    return logger


def init_logging():
    """Initialize logging (sets date string for log filenames)."""
    global _date_str, _initialized
    _date_str = datetime.now().strftime("%Y-%m-%d")
    _initialized = True


def log_command(cmd: str):
    """Track ECU header changes from commands. No file output."""
    global _active_ecu
    m = re.match(r"ATSH([0-9A-Fa-f]{3})", cmd)
    if m:
        tx_id = int(m.group(1), 16)
        lookup = _load_ecu_lookup()
        _active_ecu = lookup.get(tx_id, f"0x{tx_id:03X}")


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
