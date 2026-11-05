"""Operations that talk to an ELM327 terminal in its own language, not in UDS.

* :mod:`.repl` — ``canair repl``, the interactive adapter terminal
* :mod:`.skm_wakeup` — the Ioniq smart key module relay wake
* :mod:`.tester` — a manual TesterPresent loop (``!tester`` in the REPL)

Belongs: the live operations that exist only on an ELM327 transport (``wican-ws`` and
``elm327-tcp``) and are refused on raw ``slcan-tcp``.
Not here: the engine that speaks ELM327 (``transport/elm327/``) and the automatic keepalive
(``transport/keepalive.py``).
Imports: ``transport``, ``ecus``, ``uds`` and below, and ``live.identity``.

This package imports nothing eagerly, like ``live``.
"""
