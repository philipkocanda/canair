"""A one-line status that rewrites itself on stderr, and only on a terminal.

Used by the sweeps (``discover``, the discovery scanners, ``!tester``) for "n/total" counters,
and by ``memory dump`` for its bar, with :class:`Throughput` for the speed and the time left.
On a pipe or a log file the line would be a mess of carriage returns, so nothing is written
there. :meth:`ProgressLine.clear` removes the line, so the final report starts on a clean row.
"""

from __future__ import annotations

import re
import sys
from collections import deque
from typing import Final, TextIO

_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def visible_length(text: str) -> int:
    """The columns ``text`` takes on a terminal: its length without colour codes."""
    return len(_ESCAPE.sub("", text))


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
        shown = visible_length(text)
        self._width = max(self._width, shown)
        pad = " " * (self._width - shown)
        print(f"\r{text}{pad}", end="", file=self.stream, flush=True)

    def clear(self) -> None:
        """Erase the line, leaving the cursor at its start."""
        if self.enabled and self._width:
            print(f"\r{' ' * self._width}\r", end="", file=self.stream, flush=True)
            self._width = 0


#: How far back the speed looks, and how long a run must have gone before it is shown.
WINDOW_S: Final = 60.0
MIN_SPAN_S: Final = 3.0


class Throughput:
    """Bytes done against time; the rate over the last :data:`WINDOW_S` seconds.

    A whole-run average hides a link that recovers or degrades half way, and the speed of a
    long read changes with the block size its rate controller picks. So the speed is the bytes
    gained over a sliding window, and the time left is the bytes still to read at that speed.
    """

    def __init__(self, started: float, window_s: float = WINDOW_S, min_span_s: float = MIN_SPAN_S):
        self._window = window_s
        self._min_span = min_span_s
        self._samples: deque[tuple[float, int]] = deque([(started, 0)])

    def observe(self, now: float, done: int) -> None:
        """Record that ``done`` bytes were read by ``now`` (monotonic seconds)."""
        self._samples.append((now, done))
        # Keep the newest sample at or before the window start, so the span stays a full window.
        while len(self._samples) > 2 and self._samples[1][0] <= now - self._window:
            self._samples.popleft()

    def rate(self) -> float | None:
        """Bytes per second over the window, or ``None`` before there is enough to say."""
        then, before = self._samples[0]
        now, done = self._samples[-1]
        if now - then < self._min_span:
            return None
        return max(0, done - before) / (now - then)

    def eta(self, remaining: int) -> float | None:
        """Seconds to read ``remaining`` bytes at the current rate, or ``None`` if it is unknown."""
        rate = self.rate()
        if not rate or remaining < 0:
            return None
        return remaining / rate
