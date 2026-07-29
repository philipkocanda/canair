"""Shared hex-CLI-argument parsing for the addressing editors.

``canair ecu add`` and ``canair pids set-addressing`` both accept optional
hex-valued arguments (a target/source address byte, a flow-control id, an rx id)
and must reject a non-hex value with a consistent error. This is the single home
for that parse so the two commands stay identical and neither re-implements it.

The parser raises a neutral :class:`HexArgError` carrying a formatted message;
each command translates it to its own error idiom (``ecu add`` returns exit code
1, ``pids set-addressing`` re-raises ``SystemExit``) — so the shared parse stays
free of any one command's control-flow shape.
"""

from __future__ import annotations

_RED = "\033[91m"
_RESET = "\033[0m"


class HexArgError(ValueError):
    """A CLI hex argument that isn't valid hex. ``str(e)`` is a ready-to-print message."""


def parse_hex_arg(value: str | None, label: str) -> int | None:
    """Parse an optional hex CLI arg (``0x784``/``784``) to an int, or None.

    ``label`` is the user-facing flag name (e.g. ``fc-id``) used in the error.
    Raises :class:`HexArgError` on a non-hex value; the caller decides how to
    surface it (return code vs ``SystemExit``).
    """
    if value is None:
        return None
    try:
        return int(str(value), 16)
    except ValueError:
        raise HexArgError(
            f"{_RED}  Error: --{label} expects hex (e.g. 0x784), got {value!r}{_RESET}"
        ) from None
