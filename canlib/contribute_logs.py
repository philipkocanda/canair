"""The diagnostics log excerpt ``canair contribute --include-logs`` attaches.

The central event log (``canair logs``) records transport drops, timeouts and
internal errors. Attached to a pull request it lets a maintainer debug what the
contributor's link did, but it also records the device's address and local
paths, so every line is scrubbed before it leaves the machine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from canlib.textscan import VIN_RE

MAX_LINES = 300
MAX_CHARS = 30_000  # a GitHub PR body holds 65,536 characters in total

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_RE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){4,7}[0-9A-Fa-f]{1,4}(?![\w:])")
_MAC_RE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![\w:])")
_HOST_RE = re.compile(r"(?i)\b(?:ws|wss|http|https|tcp)://[^\s/:]+")


@dataclass
class LogExcerpt:
    lines: list[str]
    total: int  # lines in the window before truncation

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


def collect(
    since: str | None = None,
    *,
    lines: list[str] | None = None,
    max_lines: int = MAX_LINES,
    max_chars: int = MAX_CHARS,
) -> LogExcerpt:
    """The newest scrubbed event-log lines from ``since`` (``YYYY-MM-DD``) on.

    Event-log timestamps are UTC ISO-8601, so the date prefix compares as text;
    a day's margin keeps a session recorded near midnight local time.
    """
    if lines is None:
        from canlib.log import read_event_log

        lines = read_event_log()
    if since:
        lines = [ln for ln in lines if ln[:10] >= _day_before(since)]
    total = len(lines)
    kept: list[str] = []
    size = 0
    for line in reversed(lines[-max_lines:]):
        clean = scrub(line)
        if size + len(clean) + 1 > max_chars:
            break
        kept.append(clean)
        size += len(clean) + 1
    return LogExcerpt(lines=kept[::-1], total=total)


def _day_before(day: str) -> str:
    from datetime import date, timedelta

    try:
        return (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    except ValueError:
        return day


def render_markdown(excerpt: LogExcerpt) -> str:
    """The excerpt as a collapsed block for the pull request body."""
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
