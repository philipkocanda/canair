"""``canair scan`` — the scan command group.

Three kinds of scan live under one command:

  * ``canair scan range <ECU>``      — sweep a PID/DID range on one ECU (the
    general-purpose scan, with friendly ``--service`` presets + a wizard).
  * ``canair scan iocontrol <ECU>``  — IOControl discovery. Auto-selects UDS
    ``0x2F`` or KWP2000 ``0x30`` from the ECU's ``id_protocol`` and only ever sends
    the returnControlToECU probe (never an actuating option). On KWP2000 that probe
    is not proven to drive nothing, so the sweep confirms first.
  * ``canair scan routines <ECU>``   — SAFE RoutineControl (``0x31``) discovery
    (probes requestRoutineResults only).

For convenience, a bare ``canair scan <ECU>`` (or ``canair scan`` with no args)
is treated as ``canair scan range …`` — see ``_inject_default_subcommand`` in
``src/canair/cli/__init__.py``.

* :mod:`.parser` — the group and its kinds; :mod:`.run` — ``scan range``, with :mod:`.plan`
  (the flags to a scan plan) and :mod:`.wizard` (the interactive form).

Belongs: `canair scan`: the parser, the range wizard and the plan summary.
Not here: the sweeps (`live/scan/`) and the DID families and presets they use.
Imports: `live`, `ecus` and below.
"""

from .parser import NAME, SCAN_KINDS, add_parser
from .run import run_range

__all__ = ["NAME", "SCAN_KINDS", "add_parser", "run_range"]
