"""The shell's message policy, and the run's message history.

A message replaces the bottom line's context for a few seconds. An **error stays until the next
key**, so a failure that lands while the user looks away is still there when they look back. Every
message, whatever its severity, is also kept in the history that the app's event log shows, so
nothing that was said is lost when it expires.

The look of a message (its colour and glyph, no trailing period) is :mod:`canair.tui.flash`'s,
so a message reads the same in an app on the shell and in one that is not.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from rich.markup import escape

from canair.render.glyphs import FAIL, OK, WARN
from canair.tui import styles
from canair.tui.flash import Flash, Severity
from canair.tui.status import StatusItem

#: How long a message that is not an error stays on the bottom line.
MESSAGE_SECONDS = 5.0

_GLYPH = {"error": f"{FAIL} ", "warning": f"{WARN} ", "success": f"{OK} ", "info": ""}
_STYLE = {"error": styles.ERROR, "warning": styles.WARNING, "success": styles.OK, "info": ""}


@dataclass(frozen=True)
class LoggedMessage:
    at: datetime
    text: str
    severity: Severity

    def markup(self) -> str:
        """The message as one history line: the time, then the glyph and text in its colour."""
        body = escape(f"{_GLYPH[self.severity]}{self.text}")
        style = _STYLE[self.severity]
        coloured = f"[{style}]{body}[/]" if style else body
        return f"[{styles.MUTED}]{self.at:%H:%M:%S}[/]  {coloured}"


class Messages:
    """The current message and every message of the run, newest last."""

    def __init__(self, keep: int = 500) -> None:
        self._flash = Flash()
        self.history: deque[LoggedMessage] = deque(maxlen=keep)

    def show(self, text: str, severity: Severity = "info", *, secs: float | None = None) -> None:
        """Show ``text`` now; an error stays until :meth:`key_pressed`, the rest expire."""
        text = text.rstrip(".")
        expiry = None if severity == "error" else (MESSAGE_SECONDS if secs is None else secs)
        self._flash.show(text, severity, secs=expiry)
        self.history.append(LoggedMessage(datetime.now(), text, severity))

    def key_pressed(self) -> bool:
        """A key was pressed: drop an error that was waiting for one. True when one went."""
        if self._flash.live() and self._flash.severity == "error":
            self._flash.clear()
            return True
        return False

    def clear(self) -> None:
        self._flash.clear()

    @property
    def current(self) -> str:
        return self._flash.message if self._flash.live() else ""

    @property
    def severity(self) -> Severity:
        return self._flash.severity

    def item(self) -> StatusItem | None:
        """The current message as a bottom-line item, or None when there is none."""
        return self._flash.item()
