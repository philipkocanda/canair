"""One home for canair's hand-rolled ANSI escape codes and the "should this be
coloured?" policy.

Historically each command module declared its own palette (`_BOLD`/`_DIM`/…) and,
in a few places, its own `_use_color`/`_c` helpers. The codes were identical
everywhere — this leaf simply consolidates them — but the *gating* was only
implemented in the four modules that thought to add it, which is why most
commands leak escapes into a pipe and none of them honour ``NO_COLOR``.

Consumers use :func:`c` (or :func:`cerr` for stderr writes) instead of hand-
wrapping with reset; the wrap is dropped when :func:`use_color` says colour is
off. The policy is: ``FORCE_COLOR`` wins, then ``NO_COLOR``, then the stream's
own TTY-ness — the widely-honoured convention.

This module imports only stdlib; every other module in ``canlib/`` may import it.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

# The palette — the seven codes actually in use across the tree.
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def use_color(stream: TextIO | None = None) -> bool:
    """Return ``True`` when output on *stream* should carry ANSI escapes.

    Resolution order (matches the ``NO_COLOR``/``FORCE_COLOR`` conventions):

    1. ``FORCE_COLOR`` set to any non-empty value → yes (even into a pipe).
    2. ``NO_COLOR`` set to any non-empty value → no (even on a TTY).
    3. Otherwise, ``stream.isatty()``.

    ``stream`` defaults to ``sys.stdout``. Pass ``sys.stderr`` for a stderr-gated
    check (the ``cerr`` helper does this for you).
    """
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    if stream is None:
        stream = sys.stdout
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def c(text: str, code: str, *, stream: TextIO | None = None) -> str:
    """Wrap *text* in ANSI *code* + reset, or return it plain when colour is off.

    Gated on :func:`use_color` for *stream* (defaults to ``sys.stdout``).
    """
    return f"{code}{text}{RESET}" if use_color(stream) else text


def cerr(text: str, code: str) -> str:
    """Like :func:`c`, but gated on ``sys.stderr`` (warnings go to stderr)."""
    return c(text, code, stream=sys.stderr)
