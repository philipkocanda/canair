"""The Textual apps: one sub-package per screen a command opens on a TTY.

The apps are ``monitor``, ``monitor_select`` (its picker), ``captures_step`` (the stepper),
``decode_plot`` (the explorer), ``sniff`` and ``iocontrol`` (``canair io``). Each has its charter,
an ``app`` module and no eager imports. Other packages import an app module by its full path.

Belongs: a whole Textual app or screen of one command, built on the shared kit in ``tui/``, and
the view model of an app that its command also renders off a TTY (``decode_plot/model.py``,
``captures_step/model.py`` and the modules they draw with), kept in modules without Textual.
Not here: the data an app shows (the monitor and IOControl controllers in ``live/``, the frame
statistics in ``analysis/``), the command that opens it (``cli/commands/``) and the shared kit.
Imports: the kit (``tui``) and every package below the CLI. Never ``cli``.
"""
