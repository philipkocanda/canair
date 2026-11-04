"""The raw family: SLCAN over TCP with ISO-TP done client-side.

Belongs: the SLCAN bus (``slcan_tcp``), the ISO-TP stack (``isotp_stack``), the pipelined UDS
client (``uds_client``), the ``Terminal`` adapter (``terminal``) and the functional sweep
(``functional``).
Not here: ELM327 text semantics (``elm327/``), which is independent of this package.
Imports: the transport base (``errors``, ``stats``, ``keepalive``, ``functional``, ``isotp_params``),
``safety``, ``uds`` and ``core``. An import contract pins that it never imports ``elm327``.
"""
