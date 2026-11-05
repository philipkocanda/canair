"""``canair scan``: sweeps that probe an ECU for what it supports. All of them are safe probes.

* :mod:`.range` — a range of PIDs or DIDs (``scan range``)
* :mod:`.sessions` — which diagnostic sessions an ECU opens
* :mod:`.routines` and :mod:`.kwp_routines` — which routine ids exist (UDS ``0x31``, KWP ``0x33``)
* :mod:`.iocontrol` and :mod:`.kwp_iocontrol` — which IOControl ids exist (UDS ``0x2F``,
  KWP ``0x30``)
* :mod:`.discovery` — the loop the routine and IOControl scanners share
* :mod:`.presets` — the named services and per-ECU defaults (the CLI parser imports this)
* :mod:`.progress` — the progress file that lets a killed scan be resumed

Belongs: probing an ECU over a ``Terminal`` and recording the hits.
Not here: the actuation itself (``live/actuate/``), the id-range grammar (``core/id_ranges.py``),
the DID families the sweep covers (``ecus/did_families.py``) and the flags (``cli/commands/scan``).
Imports: ``transport`` (the protocol only), ``safety``, ``captures``, ``ecus``, ``render``,
``uds`` and below.

This package imports nothing eagerly, so ``presets`` stays a cheap import for the CLI parser.
"""
