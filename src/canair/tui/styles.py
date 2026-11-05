"""The Rich style names and the dialog CSS the TUIs draw with, one meaning each.

These are the colour meanings of the cli-tui-standards skill, so a view reads the same as
the plain output. A TUI that needs a colour for a new meaning adds it here and to its
``HELP_LEGEND``; it never writes a colour name inline.
"""

from __future__ import annotations

from typing import Final

ERROR: Final = "red"  # error, failure, a data-integrity fault
WARNING: Final = "yellow"  # warning, needs attention, unverified, a negative response
OK: Final = "green"  # success, verified, answered this cycle
NAME: Final = "cyan"  # a name the user can type back: ECU, PID, state, profile
HEADING: Final = "bold"  # a heading, or emphasis on a value
HEADING_NAME: Final = "bold cyan"  # a heading that names an entity
MUTED: Final = "dim"  # secondary text: labels, units, sources, tips
HELD: Final = "dim"  # a value held from an earlier read (stale)
RECORDING: Final = "bold"  # the recording indicator: red would read as an error


def dialog_css(screen: str, prefix: str, *, width: int, border: str = "$accent") -> str:
    """The CSS of a modal dialog: the box, title, hint and button row for ``#<prefix>-*``.

    Every dialog is a bordered box over a dimmed screen with a bold title, a hint line at the
    bottom and its buttons on the right. The border is ``$accent``, or ``$error`` for a
    destructive one. Theme variables only, because the apps pin ``ansi-dark``.
    """
    return f"""
    {screen} {{ align: center middle; background: $background 60%; }}
    #{prefix}-box {{
        width: {width}; max-width: 90%; height: auto; max-height: 90%; padding: 1 2;
        border: round {border}; background: $surface;
    }}
    #{prefix}-title {{ text-style: bold; margin-bottom: 1; }}
    #{prefix}-hint {{ color: $text-muted; margin-top: 1; width: 100%; height: auto; }}
    #{prefix}-buttons {{ height: auto; align-horizontal: right; margin-top: 1; }}
    #{prefix}-buttons Button {{ margin-left: 2; }}
    """
