"""The terminal's width and kind, for plain output that lays out its own columns."""

from __future__ import annotations

import shutil
import sys


def terminal_columns(default: int = 120) -> int:
    """Best-effort terminal width for layout."""
    return shutil.get_terminal_size(fallback=(default, 24)).columns


def is_interactive() -> bool:
    """True when both stdin and stdout are a terminal, as a full-screen view needs."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def truncate_text(text: str, width: int) -> str:
    """Cut ``text`` to ``width`` characters, ending in one ``…`` when it was shortened."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"
