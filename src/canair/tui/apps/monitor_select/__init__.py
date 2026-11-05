"""The ECU, then PID picker of ``canair monitor``, before the run and from its ``p`` key.

* :mod:`.app` — the picker screen and the app that runs it before any connection exists

Belongs: the picker's Textual screens.
Not here: the choice and selector logic (``live/monitor/select.py``), the monitor's own screens
(``tui/apps/monitor/``) and the flags (``cli/commands/monitor``).
Imports: the kit (``tui``), ``live.monitor.select`` and the definitions. Never ``cli``.

This package imports nothing eagerly.
"""
