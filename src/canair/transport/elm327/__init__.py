"""The ELM327 family: an adapter that does ISO-TP itself, spoken to in AT commands.

Belongs: the shared ELM327 engine (``terminal``), its policies (``pipe``, ``frame_count``,
``addressing``, ``session``, ``eeprom``, ``commands``, ``functional``) and the wire bindings
(``channel``, ``tcp``, ``wican``).
Not here: client-side ISO-TP and raw SLCAN (``raw/``), which is independent of this package.
Imports: the transport base (``errors``, ``stats``, ``keepalive``, ``functional``, ``isotp_params``),
``safety``, ``uds`` and ``core``. An import contract pins that it never imports ``raw``.
"""
