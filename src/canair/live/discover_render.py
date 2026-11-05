"""How ``canair discover`` and ``canair identity`` colour their report rows.

The row markers and the reply statuses, coloured by what they mean for the ECU. The painter and
the column layout they go through are :mod:`canair.render.paint`.
"""

from __future__ import annotations

from canair.render import ansi
from canair.render.paint import Cell

# Row markers and statuses, coloured by what they mean for the ECU.
POSITIVE: Cell = ("+", ansi.raw.GREEN)
REFUSED: Cell = ("~", ansi.raw.YELLOW)
NO_ANSWER: Cell = ("?", ansi.raw.RED)


def status_cell(status: str) -> Cell:
    """A reply's status (``positive``, ``NRC 0xNN``, ``pending``) in its colour."""
    if status == "positive":
        return status, ansi.raw.GREEN
    if status.startswith("NRC"):
        return status, ansi.raw.YELLOW
    return status, ansi.raw.RED
