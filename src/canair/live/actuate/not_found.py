"""The error for an ECU, DID or RID that the profile does not define (``io`` and ``routines``)."""

from __future__ import annotations

import sys
from collections.abc import Iterable

from canair.render import ansi, messages


def report_not_found(message: str, available: Iterable[str], noun: str) -> None:
    """Print ``error: message`` and, when there are any, a hint naming the ``available`` ones.

    The caller ends the command with exit 1: a lookup that finds nothing is a failure.
    """
    messages.error(message)
    names = [ansi.c(name, ansi.raw.CYAN, stream=sys.stderr) for name in sorted(available)]
    if names:
        messages.hint(f"{noun}: {', '.join(names)}")
