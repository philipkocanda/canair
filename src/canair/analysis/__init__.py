"""Offline analysis over captures and definitions: alignment, correlation, counters, mirrors, stats.

Belongs: pure computation over loaded captures and profile definitions, with no device, no
flags and no output: time alignment and fill, correlation, mirrors, counters, support maps,
triage, the blind-rediscovery strip (``blind``), and physical-plausibility bands (``physical``).
Not here: reading the store (``captures/``), rendering (the command's ``render.py``) and
anything that talks to the car (``live/``).
Imports: ``captures``, ``ecus``, ``states``, ``expression``, ``uds`` and ``core``.
"""
