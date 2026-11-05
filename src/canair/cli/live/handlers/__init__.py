"""The live handlers: what each live subcommand does once it is connected.

A handler reads the parsed flags and calls one ``mode_*`` coroutine with typed arguments. The
command picks its handler (see :class:`canair.cli.live.runtime.LiveOperation`); nothing
selects one by inspecting which flags are set. Grouped by family:

* :mod:`.reads`       — identity, named signals, raw hex.
* :mod:`.multi`       — the ``read``/``monitor`` mini-language steps.
* :mod:`.scans`       — PID/DID sweeps, routine and IOControl probes, discovery.
* :mod:`.actuators`   — IOControl, routines and profile actions (can move hardware).
* :mod:`.diagnostics` — DTC read, clear and scan-all.
* :mod:`.elm_only`    — the interactive REPL.
* :mod:`.ranges`      — the hex-range flag parsers.

Every handler has the signature ``(args, terminal, pids_data, host, *, reconnect=None)``.

Belongs: the code that reads a live command's parsed flags and calls one ``mode_*`` coroutine
with typed arguments, plus the :class:`~canair.cli.live.operation.LiveOperation` each
subcommand runs.
Not here: the operations themselves (``live/``), the session and its error policy
(``modes/session.py``), and flag declarations (the command modules).
Imports: ``modes`` and everything below it. Handlers are the only code between the CLI and
``modes`` that sees an ``argparse.Namespace``.
"""
