"""``canair correlate`` — cross-signal correlation across a drive/session.

Builds every decoded signal + every varying raw byte across all co-polled
ECU/PIDs in scope, time-aligns them by nearest timestamp, and ranks the strongest
cross-signal relationships. The "show me every strong relationship in this drive"
entry point — how the AAF-speed and MCU-temp finds were made by hand.

Read-only analysis over ``captures/``; talks to no device.

Two domains behind one command, split by *kind* (a bare ``canair correlate …`` is
shorthand for ``uds``, injected by ``cli.py``'s ``_GROUP_DEFAULTS``):

* :mod:`.uds` — diagnostic captures (domain A): the ranked list and its
  ``--against``/``--control``/``--matrix``/``--overlap``/``--find-mirrors`` variants.
* :mod:`.can` — an imported raw broadcast-CAN frame log (domain B): the same core
  over ``0xID:rN`` byte series.

with the supporting layers :mod:`.parser` (argparse), :mod:`.series` (building the
series to rank), :mod:`.calc` (numeric helpers), :mod:`.render` (output), and
:mod:`.promote` (persisting a hit, and warning on a thin join).
"""

from .parser import NAME, add_parser
from .uds import run

__all__ = ["NAME", "add_parser", "run"]
