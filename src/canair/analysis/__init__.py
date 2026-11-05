"""Offline analysis over captures and definitions: alignment, correlation, counters, mirrors, stats.

Belongs: pure computation over loaded captures and profile definitions, with no device, no
flags and no output: time alignment and fill, correlation, mirrors, counters, support maps,
triage, the blind-rediscovery strip (``blind``), physical-plausibility bands (``physical``),
and the per-ID statistics the sniffer keeps of the frames it sees (``sniff_stats``).
Not here: reading the store (``captures/``), rendering (the command's ``render.py``) and
anything that talks to the car (``live/``).
Imports: the store (``captures``), the definitions it computes over (``ecus``, ``states``,
``variants``, ``profile``, ``query``, ``privacy``), ``expression``, ``uds`` and ``core``.
Nothing in those imports it back.
"""
