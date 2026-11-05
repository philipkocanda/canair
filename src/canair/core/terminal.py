"""The terminal's size, for code that lays out its own screen without Textual."""

from __future__ import annotations

import shutil


def terminal_lines(default: int = 24) -> int:
    """Best-effort terminal height for viewport sizing."""
    return shutil.get_terminal_size(fallback=(120, default)).lines


def terminal_columns(default: int = 120) -> int:
    """Best-effort terminal width for layout."""
    return shutil.get_terminal_size(fallback=(default, 24)).columns
