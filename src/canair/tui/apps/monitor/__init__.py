"""The live monitor's Textual app: the table, its keys and its modals.

* :mod:`.app` — the app and its screens
* :mod:`.dialogs` — the form dialogs (label a recording, edit a signal)
* :mod:`.overlays` — the read-only overlays and the session-type picker

Belongs: what the monitor shows and how its keys act on the controller.
Not here: polling, recording and the controller (``live/monitor/``), the picker before a run
(``tui/apps/monitor_select/``) and the flags (``cli/commands/monitor``).
Imports: the kit (``tui``), ``live.monitor`` and the packages below it. Never ``cli``.

This package imports nothing eagerly.
"""
