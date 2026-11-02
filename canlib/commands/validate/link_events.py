"""captures/ validation — payloads timed inside a recorded disconnect.

A closed ``disconnect`` link event (:mod:`canlib.captures.link_events`) spans the time in
which no ECU answered coherently, so a payload timed strictly inside one contradicts it:
either the capture's clock or the event's bounds are wrong. One warning per session.
"""

from __future__ import annotations

from pathlib import Path

from canlib.captures import io as capture_io
from canlib.captures.link_events import gap_intervals, in_closed_gap, session_link_events
from canlib.states.spans import parse_time_key

from .captures import CaptureWarning


def link_event_warnings(path: Path) -> list[CaptureWarning]:
    """Sessions in ``path`` with payload captures timed inside one of their disconnects."""
    warnings: list[CaptureWarning] = []
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        gaps = gap_intervals(session_link_events(session))
        if not gaps:
            continue
        inside = 0
        for cap in session.get("captures", []) or []:
            if not isinstance(cap, dict) or not cap.get("payload"):
                continue
            key = parse_time_key(cap.get("time"))
            if key is not None and in_closed_gap(gaps, key):
                inside += 1
        if inside:
            warnings.append(
                CaptureWarning(
                    f"sessions[{si}]",
                    f"{inside} payload capture(s) timed inside a recorded disconnect — "
                    "nothing answered then, so the capture times or the link_events bounds "
                    "are wrong",
                )
            )
    return warnings
