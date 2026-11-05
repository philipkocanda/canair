"""``canair bix`` — convert byte indices between WiCAN, ISO-TP, Torque, OBDb.

* :mod:`.parser` — the flags and the ``--help`` text; :mod:`.entry` picks the view.
* :mod:`.convert` — one index to every notation; :mod:`.table` — the full table and the
  bare-command overview.
* :mod:`.annotate` — a real payload byte by byte, with :mod:`.payload` (reading the hex and
  checking its first byte) and :mod:`.subfunction` (the width and layout it implies).

Belongs: `canair bix`: the parser and the printing of each view.
Not here: the byte indices and layouts themselves (`uds/`, `expression/`).
Imports: `expression`, `uds` and `ecus`.
"""

from .entry import run
from .parser import NAME, add_parser

__all__ = ["NAME", "add_parser", "run"]
