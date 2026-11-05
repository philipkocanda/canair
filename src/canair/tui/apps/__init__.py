"""The Textual apps: one module per screen a command opens on a TTY.

Belongs: a whole Textual app or screen of one command (the monitor, its picker, the capture
stepper, the decode plot explorer, the sniffer table), built on the shared kit in ``tui/``,
and the view model of an app that its command also renders off a TTY (``decode_plot_model``,
``captures_step_model`` and the modules they draw with), kept in modules without Textual.
Not here: the data an app shows (the monitor controller in ``live/``, the frame statistics in
``analysis/``), the command that opens it (``cli/commands/``) and the shared kit.
Imports: the kit (``tui``) and every package below the CLI. Never ``cli``.
"""
