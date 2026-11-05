"""One transient status-bar message with a severity, shared by the apps.

A failure must never look like a success, so every message states its severity: the colour
follows the skill's meanings and a glyph carries it for readers who cannot see the colour.
A message has no trailing period.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from rich.markup import escape

from canair.render.glyphs import FAIL, OK, WARN
from canair.tui import styles
from canair.tui.status import P_ESSENTIAL, StatusItem

Severity = Literal["error", "warning", "success", "info"]

_LOOK: dict[str, tuple[str, str]] = {
    "error": (styles.ERROR, f"{FAIL} "),
    "warning": (styles.WARNING, f"{WARN} "),
    "success": (styles.OK, f"{OK} "),
    "info": ("", ""),
}


def severity_of(message: str, ok: Severity = "success") -> Severity:
    """The severity of a result message that a controller returned as bare text.

    The live controllers hand back one-line results ("Saved …", "Edit failed: …"). The words
    say whether it worked, so the app reads them rather than showing every one as a success.
    """
    text = message.lower()
    if "failed" in text or text.startswith(("could not", "cannot", "invalid", "unknown")):
        return "error"
    if text.startswith(("no ", "nothing")) or ": no " in text or "needs" in text:
        return "warning"
    return ok


@dataclass
class Flash:
    """The message an app shows in its status bar until it expires or is replaced."""

    message: str = ""
    severity: Severity = "info"
    expires: float | None = None

    def show(self, message: str, severity: Severity = "info", *, secs: float | None = None) -> None:
        self.message = message.rstrip(".")
        self.severity = severity
        self.expires = None if secs is None else time.monotonic() + secs

    def clear(self) -> None:
        self.message = ""
        self.expires = None

    def live(self) -> bool:
        """Whether there is a message to show; an expired one is dropped."""
        if self.message and self.expires is not None and time.monotonic() >= self.expires:
            self.clear()
        return bool(self.message)

    def item(self) -> StatusItem | None:
        if not self.live():
            return None
        style, glyph = _LOOK[self.severity]
        text = escape(f"{glyph}{self.message}")
        return StatusItem(f"[b {style}]{text}[/]" if style else text, P_ESSENTIAL)
