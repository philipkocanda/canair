"""``canair discover``: which ECUs answer, one at a time or all at once on the functional id.

* :mod:`.physical` — probe every known and candidate request id, optionally register what answers
* :mod:`.functional` — one read on the functional id, grouping the responders by CAN bus; several
  rounds print as one map
* :mod:`.sweep` — classifying a functional round (pure)

Belongs: finding ECUs on the bus and reporting them.
Not here: how a reply is coloured (``render/discover.py``), the profile edit that registers an ECU
(``ecus/``) and the flags (``cli/commands/discover``).
Imports: ``transport`` (the protocol only), ``safety``, ``captures``, ``ecus``, ``render``,
``uds`` and below, and ``live.identity``.

This package imports nothing eagerly, like ``live``.
"""
