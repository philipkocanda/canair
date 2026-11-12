"""The live monitor's Textual app: the table, its keys and its modals.

* :mod:`.app` — the app and its screens
* :mod:`.panes` — the focusable regions and the keys each owns while it has focus
* :mod:`.actuators` — the actuator pane (``--actuate``) and the app's actions for its keys
* :mod:`.commands` — the menus: every command, its key and the action it runs
* :mod:`.status` — what the status line, the menu bar's title and the bottom line say, as
  functions over the controller
* :mod:`.polling`, :mod:`.recording` and :mod:`.editing` — the app's poll loop, its save keys
  and its signal cursor and edit keys, as mixins
* :mod:`.dialogs` — the form dialogs (label a recording, edit a signal)
* :mod:`.overlays` — the read-only overlays and the session-type picker

Belongs: what the monitor shows and how its keys act on the controller.
Not here: polling, recording and the controller (``live/monitor/``), the picker before a run
(``tui/apps/monitor_select/``) and the flags (``cli/commands/monitor``).
Imports: the kit (``tui``), ``live.monitor`` and the packages below it. Never ``cli``.

This package imports nothing eagerly.
"""
