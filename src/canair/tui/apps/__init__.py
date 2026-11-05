"""The Textual apps: one module per screen a command opens on a TTY.

Belongs: a whole Textual app or screen of one command (the monitor, its picker, the capture
stepper, the decode plot explorer, the sniffer table), built on the shared kit in ``tui/``.
Not here: the state an app shows (the monitor controller in ``live/``, a command's model in the
package that owns its data), the command that opens it (``cli/commands/``) and the shared kit.
Imports: the kit (``tui``) and every package below the CLI. Never ``cli``.
"""
