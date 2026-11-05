"""How ``canair discover`` and ``canair identity`` colour their report rows.

The row markers and the reply statuses, coloured by what they mean for the ECU. The painter and
the column layout they go through are :mod:`canair.render.paint`.

One marker, one meaning, in every sweep (``discover``, ``scan iocontrol|routines|sessions``):

* ``+`` the probe was answered positively.
* ``~`` it was answered with a negative response that proves the ECU, DID or session exists.
* ``-`` there is nothing there: no answer, or a negative response saying it is absent.
* ``!`` the probe itself failed (a transport error).

``?`` is not a sweep marker: in a signal table it means "unverified".
"""

from __future__ import annotations

from canair.render import ansi
from canair.render.paint import Cell

# Row markers and statuses, coloured by what they mean for the ECU.
POSITIVE: Cell = ("+", ansi.raw.GREEN)
REFUSED: Cell = ("~", ansi.raw.YELLOW)
ABSENT: Cell = ("-", ansi.raw.DIM)
NO_ANSWER: Cell = ABSENT
PROBE_FAILED: Cell = ("!", ansi.raw.RED)


def status_cell(status: str) -> Cell:
    """A reply's status (``positive``, ``NRC 0xNN``, ``pending``) in its colour."""
    if status == "positive":
        return status, ansi.raw.GREEN
    if status.startswith("NRC"):
        return status, ansi.raw.YELLOW
    return status, ansi.raw.RED
