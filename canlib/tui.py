"""Small terminal helpers shared across modes.

Kept intentionally dependency-light so any mode can import it. The live monitor
now uses Textual (:mod:`canlib.modes._monitor_tui`); the remaining consumers here
are the IOControl / routines TUIs (viewport sizing) and the capture stepper
(single-key reads).
"""

from __future__ import annotations

import os
import shutil

__all__ = [
    "read_key_raw",
    "terminal_columns",
    "terminal_lines",
]


def terminal_lines(default: int = 24) -> int:
    """Best-effort terminal height for viewport sizing."""
    return shutil.get_terminal_size(fallback=(120, default)).lines


def terminal_columns(default: int = 120) -> int:
    """Best-effort terminal width for layout."""
    return shutil.get_terminal_size(fallback=(default, 24)).columns


def read_key_raw(fd: int) -> str:
    """Blocking read of a single keypress / escape sequence from a cbreak fd."""
    return os.read(fd, 16).decode("utf-8", errors="ignore")
