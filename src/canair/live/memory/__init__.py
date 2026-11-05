"""Reading ECU memory over a ``Terminal``: ``canair scan memory`` and ``canair memory dump``.

Both send only ReadMemoryByAddress (``0x23``), a read, to one ECU on its physical
address. Neither unlocks anything: a locked region is reported as locked and left alone.

* :mod:`.reads` — the ECU a command targets, one checked memory read, the session.
* :mod:`.scan` — map which address ranges answer (``canair scan memory``).
* :mod:`.dump` — read ranges into a local dump (``canair memory dump``).

Belongs: memory reads over a ``Terminal`` and their stop rules.
Not here: the dump store (``memory/``), the request and reply rules (``uds/memory.py``) and the
flags (``cli/commands/memory``, ``cli/commands/scan``).
Imports: ``memory``, ``ecus``, ``yaml_edit`` (the scan's write-back), ``transport`` (the
protocol only) and ``uds``.
"""
