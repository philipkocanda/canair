"""``canair read`` and ``canair raw``: run query steps against the car once and print the result.

* :mod:`.multi` — the orchestrator, ``mode_multi`` (a step list, a ``--json`` document)
* :mod:`.exec` — the per-step primitives: session, query, raw, IOControl and routine steps
* :mod:`.batch` — multi-DID batching and the result kernel the monitor reuses
* :mod:`.repl` — the interactive prompt of ``read``
* :mod:`.multi_json` — the ``--json`` document
* :mod:`.param` — reading signals by name
* :mod:`.raw` — ``canair raw``, hex in and hex out
* :mod:`.raw_ops` — running a live command over the raw ``slcan-tcp`` transport

Belongs: executing a step pipeline over a ``Terminal`` and reporting its results.
Not here: the step grammar (``query/``), the polling monitor (``live/monitor/``), what may be
sent (``safety/``) and the flags (``cli/commands/``).
Imports: ``transport``, ``safety``, ``query``, ``captures``, ``ecus``, ``render``, ``uds`` and
below, and the other ``live`` families the steps can reach.

This package imports nothing eagerly, like ``live``.
"""
