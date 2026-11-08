"""Reading ECU memory over a ``Terminal``: ``canair scan memory``, ``memory dump`` and
``memory image``.

They read memory only with ReadMemoryByAddress (``0x23``), to one ECU on its physical
address. None unlocks anything: a locked region is reported as locked and left alone.

* :mod:`.reads` — the ECU a command targets, the checked block reader, the session.
* :mod:`.scan` — map which address ranges answer (``canair scan memory``).
* :mod:`.dump` — read ranges into a local dump (``canair memory dump``).
* :mod:`.image` — plan and read a whole memory image as a resumable job
  (``canair memory image``), with :mod:`.survey` (map, aliases, survey), :mod:`.extents`
  (the extent passes, pause and continuity) and :mod:`.liveness` (health, state, waiting).

Belongs: memory reads over a ``Terminal``, their stop and pause rules.
Not here: the dump store and the pure job decisions (``memory/``), the request and reply rules
(``uds/memory.py``) and the flags (``cli/commands/memory``, ``cli/commands/scan``).
Imports: ``memory``, ``ecus``, ``states``, ``expression`` (the state's signals),
``live.dtc`` and ``live.identity`` (the fault codes and the software version), ``yaml_edit``
(the scan's write-back), ``transport`` (the protocol only) and ``uds``.
"""
