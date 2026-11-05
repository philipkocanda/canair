"""A one-line status that rewrites itself on stderr, and only on a terminal.

Used by the sweeps (``discover``, the discovery scanners, ``!tester``) for "n/total" counters.
On a pipe or a log file the line would be a mess of carriage returns, so nothing is written
there. :meth:`ProgressLine.clear` removes the line, so the final report starts on a clean row.
"""

from __future__ import annotations

import sys
from typing import TextIO


class ProgressLine:
    """Rewrites one status line on ``stream`` (stderr) while it is a terminal."""

    def __init__(self, stream: TextIO | None = None):
        self.stream = stream or sys.stderr
        isatty = getattr(self.stream, "isatty", None)
        self.enabled = bool(isatty and isatty())
        self._width = 0

    def update(self, text: str) -> None:
        """Replace the line with ``text``."""
        if not self.enabled:
            return
        self._width = max(self._width, len(text))
        print(f"\r{text.ljust(self._width)}", end="", file=self.stream, flush=True)

    def clear(self) -> None:
        """Erase the line, leaving the cursor at its start."""
        if self.enabled and self._width:
            print(f"\r{' ' * self._width}\r", end="", file=self.stream, flush=True)
            self._width = 0
