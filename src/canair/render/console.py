"""The one place canair builds a Rich console for plain (non-Textual) output.

Rich decides colour on its own: ``NO_COLOR`` keeps bold and dim, an empty ``FORCE_COLOR`` turns
colour off, a piped stdout wraps at 80 columns, and numbers, paths and addresses get coloured
unprompted. canair's other output follows :mod:`canair.render.ansi` instead. A console from
here follows the same policy, so one command never prints in two different ways:

- colour is on exactly when :func:`~canair.render.ansi.use_color` says so for the stream the
  console writes to, and off means *no* styling at all (not even bold);
- nothing is highlighted on its own, so only the markup a caller wrote is coloured;
- a piped console does not wrap at 80 columns (an explicit ``COLUMNS`` still wins).

Build the console where it is used, not at import: the answer depends on the stream and the
environment at the moment of printing. Escape text that came from data (names, paths, notes,
error text) with :func:`escape` before it goes into markup.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

from rich.console import Console
from rich.markup import escape

from canair.render import ansi

__all__ = ["console", "escape"]

#: Wide enough that a line from a piped console is never cut.
_PIPED_WIDTH = 100_000


def console(*, stderr: bool = False, file: TextIO | None = None) -> Console:
    """A console for stdout, stderr or ``file`` that follows canair's colour and width policy."""
    stream = file or (sys.stderr if stderr else sys.stdout)
    color = ansi.use_color(stream)
    isatty = getattr(stream, "isatty", None)
    interactive = bool(isatty and isatty())
    return Console(
        file=file,
        stderr=stderr,
        highlight=False,
        force_terminal=color,
        color_system="auto" if color else None,
        width=None if interactive or "COLUMNS" in os.environ else _PIPED_WIDTH,
    )
