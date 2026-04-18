"""Command and response logging to date-stamped files.

Logs are written per-ECU (based on the active ATSH header) and to a general
file for untargeted commands (AT commands, session init, etc.).

Directory structure:
    logs/
        commands-YYYY-MM-DD.log       -- all commands (general log)
        responses-YYYY-MM-DD.log      -- all responses (general log)
        ecu/
            BMS-YYYY-MM-DD.log        -- per-ECU responses
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

_cmd_logger: logging.Logger | None = None
_resp_logger: logging.Logger | None = None
_ecu_loggers: dict[str, logging.Logger] = {}  # ecu_name -> logger
_active_ecu: str | None = None  # current ECU name (from ATSH header)
_ecu_lookup: dict[int, str] | None = None  # tx_id -> name cache
_date_str: str = ""


def _load_ecu_lookup() -> dict[int, str]:
    """Lazy-load ECU TX ID → name mapping from ecus.yaml."""
    global _ecu_lookup
    if _ecu_lookup is not None:
        return _ecu_lookup
    try:
        from .pids import load_ecus
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
    """Initialize date-stamped command and response log files.

    Creates:
        logs/commands-YYYY-MM-DD.log   -- every command sent to the WiCAN
        logs/responses-YYYY-MM-DD.log  -- every response received from the WiCAN
        logs/ecu/<ECU>-YYYY-MM-DD.log  -- per-ECU responses (created on first use)
    """
    global _cmd_logger, _resp_logger, _date_str

    LOG_DIR.mkdir(exist_ok=True)
    _date_str = datetime.now().strftime("%Y-%m-%d")

    _cmd_logger = logging.getLogger("can_request.commands")
    _cmd_logger.setLevel(logging.INFO)
    _cmd_logger.propagate = False
    if not _cmd_logger.handlers:
        fh = logging.FileHandler(LOG_DIR / f"commands-{_date_str}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(message)s"))
        _cmd_logger.addHandler(fh)

    _resp_logger = logging.getLogger("can_request.responses")
    _resp_logger.setLevel(logging.INFO)
    _resp_logger.propagate = False
    if not _resp_logger.handlers:
        fh = logging.FileHandler(LOG_DIR / f"responses-{_date_str}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(message)s"))
        _resp_logger.addHandler(fh)


def _update_active_ecu(cmd: str):
    """Track ECU header changes from ATSH commands."""
    global _active_ecu
    m = re.match(r"ATSH([0-9A-Fa-f]{3})", cmd)
    if m:
        tx_id = int(m.group(1), 16)
        lookup = _load_ecu_lookup()
        _active_ecu = lookup.get(tx_id, f"0x{tx_id:03X}")


def log_command(cmd: str):
    """Log a command with ISO 8601 timestamp."""
    _update_active_ecu(cmd)
    if _cmd_logger:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        _cmd_logger.info(f"[{ts}] {cmd}")


def log_response(cmd: str, response: str):
    """Log a response with ISO 8601 timestamp and the command that triggered it.

    Written to both the general response log and the active ECU's log file.
    """
    if _resp_logger:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        resp_oneline = response.replace("\n", " | ")
        line = f"[{ts}] {cmd} -> {resp_oneline}"
        _resp_logger.info(line)

        # Also log to per-ECU file (skip AT commands without active ECU)
        if _active_ecu and not cmd.startswith("AT"):
            ecu_logger = _get_ecu_logger(_active_ecu)
            ecu_logger.info(line)
