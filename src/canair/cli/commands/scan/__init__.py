"""``canair scan`` — the scan command group.

Five kinds of scan live under one command, one module per concern:

  * ``canair scan range <ECU>``      — sweep a PID/DID range on one ECU (the
    general-purpose scan, with friendly ``--service`` presets + a wizard;
    :mod:`.range_parser`, :mod:`.plan`, :mod:`.wizard`, :mod:`.run`).
  * ``canair scan iocontrol <ECU>``  — IOControl discovery. Auto-selects UDS
    ``0x2F`` or KWP2000 ``0x30`` from the ECU's ``id_protocol`` and only ever sends
    the returnControlToECU probe (never an actuating option). On KWP2000 that probe
    is not proven to drive nothing, so the sweep confirms first (:mod:`.probes`).
  * ``canair scan routines <ECU>``   — SAFE RoutineControl (``0x31``) discovery
    (probes requestRoutineResults only; :mod:`.probes`).
  * ``canair scan sessions <ECU>``   — SAFE session-type discovery (:mod:`.probes`).
  * ``canair scan memory <ECU>``     — map which address ranges answer ReadMemoryByAddress
    (``0x23``), a read; asks first (:mod:`.memory_kind`).

For convenience, a bare ``canair scan <ECU>`` (or ``canair scan`` with no args)
is treated as ``canair scan range …`` — see ``_inject_default_subcommand`` in
``src/canair/cli/__init__.py``.

Belongs: `canair scan`: each kind's parser, the range plan and wizard, the confirmation.
Not here: the sweeps over the car (`live/`) and their result sections (`yaml_edit/`).
Imports: `live` (its presets and handlers) and below.
"""

from .parser import NAME, SCAN_KINDS, add_parser
from .run import run_range

__all__ = ["NAME", "SCAN_KINDS", "add_parser", "run_range"]
