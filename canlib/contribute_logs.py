"""The diagnostics log excerpt ``canair contribute --include-logs`` attaches.

The central event log (``canair logs``) records transport drops, timeouts and
internal errors. Attached to a pull request it lets a maintainer debug what the
contributor's link did, but it also records the device's address and local
paths, so every line is scrubbed before it leaves the machine.

The log is shared by every canair session, so only the events written while a
contributed session was recording are attached: each session's span from its
first to its last capture, widened by a margin for the connect before and the
shutdown after. Capture times are local wall-clock and event times UTC, so the
spans are converted with this machine's time zone, the one they were recorded in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from canlib.textscan import VIN_RE

MAX_LINES = 300
MAX_CHARS = 30_000  # a GitHub PR body holds 65,536 characters in total
SESSION_LEAD = timedelta(minutes=2)  # connecting and opening sessions precede the first capture
SESSION_TAIL = timedelta(minutes=1)

Window = tuple[datetime, datetime]

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_RE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){4,7}[0-9A-Fa-f]{1,4}(?![\w:])")
_MAC_RE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![\w:])")
_HOST_RE = re.compile(r"(?i)\b(?:ws|wss|http|https|tcp)://[^\s/:]+")


@dataclass
class LogExcerpt:
    lines: list[str]
    total: int  # lines in the sessions' windows before truncation
    sessions: int = 0  # contributed sessions the windows came from

    @property
    def truncated(self) -> bool:
        return len(self.lines) < self.total


def scrub(line: str, home: str | None = None) -> str:
    """``line`` with addresses, local paths, emails and VINs replaced by placeholders."""
    home = home if home is not None else str(Path.home())
    if home and home != "/":
        line = line.replace(home, "~")
    line = _HOST_RE.sub(lambda m: m.group(0).split("://", 1)[0] + "://<host>", line)
    line = _EMAIL_RE.sub("<email>", line)
    line = _MAC_RE.sub("<mac>", line)
    line = _IPV6_RE.sub("<ip>", line)
    line = _IPV4_RE.sub("<ip>", line)
    return VIN_RE.sub("<vin>", line)


def session_window(day: str, start: str = "", end: str = "") -> Window | None:
    """A session's UTC span, widened by the margins; its whole local day when untimed.

    ``day`` is the session's ``YYYY-MM-DD`` date and ``start``/``end`` its first
    and last capture's local ``HH:MM:SS[.mmm]``. None when ``day`` doesn't parse.
    """
    try:
        on = date.fromisoformat(day)
    except ValueError:
        return None
    try:
        lo = datetime.combine(on, time.fromisoformat(start)) - SESSION_LEAD
        hi = datetime.combine(on, time.fromisoformat(end)) + SESSION_TAIL
    except ValueError:
        lo = datetime.combine(on, time.min)
        hi = lo + timedelta(days=1)
    # A naive datetime's astimezone() reads it as local time.
    return lo.astimezone(UTC), hi.astimezone(UTC)


def session_windows(spans: Iterable[tuple[str, str, str]]) -> list[Window]:
    """:func:`session_window` of every ``(date, start, end)``, skipping undated ones."""
    return [w for w in (session_window(*span) for span in spans) if w is not None]


def collect(
    windows: Sequence[Window],
    *,
    lines: list[str] | None = None,
    max_lines: int = MAX_LINES,
    max_chars: int = MAX_CHARS,
) -> LogExcerpt:
    """The newest scrubbed event-log lines written inside any of ``windows``."""
    if lines is None:
        from canlib.log import read_event_log

        lines = read_event_log()
    lines = [ln for ln in lines if _within(_event_time(ln), windows)]
    total = len(lines)
    kept: list[str] = []
    size = 0
    for line in reversed(lines[-max_lines:]):
        clean = scrub(line)
        if size + len(clean) + 1 > max_chars:
            break
        kept.append(clean)
        size += len(clean) + 1
    return LogExcerpt(lines=kept[::-1], total=total, sessions=len(windows))


def _event_time(line: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(line.split(maxsplit=1)[0])
    except (ValueError, IndexError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _within(stamp: datetime | None, windows: Sequence[Window]) -> bool:
    return stamp is not None and any(lo <= stamp <= hi for lo, hi in windows)


def render_markdown(excerpt: LogExcerpt) -> str:
    """The excerpt as a collapsed block for the pull request body."""
    if not excerpt.sessions:
        return (
            "### Diagnostics log\n\n"
            "The contribution adds no capture sessions, so no diagnostics log is attached."
        )
    if not excerpt.lines:
        return "### Diagnostics log\n\nThe diagnostics log has no entries for these sessions."
    shown = len(excerpt.lines)
    note = f"last {shown} of {excerpt.total} lines" if excerpt.truncated else f"{shown} lines"
    body = "\n".join(excerpt.lines).replace("```", "'''")
    return (
        "### Diagnostics log\n\n"
        f"<details><summary><code>canair logs</code> ({note}, addresses and paths "
        "scrubbed)</summary>\n\n"
        f"```\n{body}\n```\n\n</details>"
    )
