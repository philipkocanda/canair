"""``canair identity``: read an ECU's identity DIDs or records and decode them.

* :mod:`.read` — the operation, one ECU at a time or every ECU
* :mod:`.decode` — turning a reply into fields, and choosing UDS or KWP2000 for an ECU
* :mod:`.records` — the standard identity DIDs and KWP2000 records

Belongs: reading identity over a ``Terminal`` and decoding what comes back.
Not here: the identity a profile stores (``ecus/``), the ECU revision it implies
(``live/revision_probe.py``) and the flags (``cli/commands/identity``).
Imports: ``transport`` (the protocol only), ``ecus``, ``uds`` and below.

This package imports nothing eagerly, like ``live``.
"""
