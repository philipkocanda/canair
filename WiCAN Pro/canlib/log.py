"""Command and response logging to date-stamped files."""

import logging
from datetime import UTC, datetime

from .constants import SCRIPT_DIR

LOG_DIR = SCRIPT_DIR / "logs"

_cmd_logger: logging.Logger | None = None
_resp_logger: logging.Logger | None = None


def init_logging():
    """Initialize date-stamped command and response log files.

    Creates:
        logs/commands-YYYY-MM-DD.log   -- every command sent to the WiCAN
        logs/responses-YYYY-MM-DD.log  -- every response received from the WiCAN
    """
    global _cmd_logger, _resp_logger

    LOG_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")

    _cmd_logger = logging.getLogger("can_request.commands")
    _cmd_logger.setLevel(logging.INFO)
    _cmd_logger.propagate = False
    if not _cmd_logger.handlers:
        fh = logging.FileHandler(LOG_DIR / f"commands-{date_str}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(message)s"))
        _cmd_logger.addHandler(fh)

    _resp_logger = logging.getLogger("can_request.responses")
    _resp_logger.setLevel(logging.INFO)
    _resp_logger.propagate = False
    if not _resp_logger.handlers:
        fh = logging.FileHandler(LOG_DIR / f"responses-{date_str}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(message)s"))
        _resp_logger.addHandler(fh)


def log_command(cmd: str):
    """Log a command with ISO 8601 timestamp."""
    if _cmd_logger:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        _cmd_logger.info(f"[{ts}] {cmd}")


def log_response(cmd: str, response: str):
    """Log a response with ISO 8601 timestamp and the command that triggered it."""
    if _resp_logger:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        resp_oneline = response.replace("\n", " | ")
        _resp_logger.info(f"[{ts}] {cmd} -> {resp_oneline}")
