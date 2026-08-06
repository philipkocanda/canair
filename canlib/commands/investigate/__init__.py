"""``canair investigate`` — one-shot "tell me everything about this PID".

For every varying data byte, reports in one ranked table whether a signal already
maps it, its state-discriminability, its strongest co-polled cross-signal anchor
(with a linear fit and a physical-unit guess), and whether any scaling lands it in
a named physical band. Bundles the manual
``coverage -> discriminate -> correlate -> hunt`` loop into a single call — the
"point it at an unknown PID" entry point.

Read-only analysis over ``captures/``; talks to no device.

Two domains behind one command, split by *kind* (a bare ``canair investigate …``
is shorthand for ``uds``, injected by ``cli.py``'s ``_GROUP_DEFAULTS``):

* :mod:`.uds` — a diagnostic PID (domain A), with ``--bits``/``--events``.
* :mod:`.can` — one arbitration ID of a raw broadcast-CAN frame log (domain B).

with :mod:`.parser` (argparse), :mod:`.report` (the per-byte record and its
scoring), and :mod:`.render` (output).
"""

from .parser import NAME, add_parser
from .uds import run

__all__ = ["NAME", "add_parser", "run"]
