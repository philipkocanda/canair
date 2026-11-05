"""``canair sniff``: the refreshing per-ID table of the CAN frames on the bus.

* :mod:`.app` — the Textual app

Belongs: the sniffer's Textual screen.
Not here: the per-ID statistics it shows (``analysis/sniff_stats.py``), the table text
(``render/sniff.py``) and the flags (``cli/commands/sniff``).
Imports: the kit (``tui``), ``render``, ``analysis`` and ``core``. Never ``cli``.

This package imports nothing eagerly.
"""
