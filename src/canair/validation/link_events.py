"""captures/ validation — payloads timed inside a recorded gap.

A closed ``disconnect``, ``paused`` or ``ecu_silent`` link event
(:mod:`canair.captures.link_events`) spans time in which nothing was gathered for its
scope, so a payload from that scope timed strictly inside one contradicts it: either the
capture's clock or the event's bounds are wrong. One warning per session.
"""

from __future__ import annotations

from pathlib import Path

from canair.captures import io as capture_io
from canair.captures.link_events import (
    ANSWER_BOUNDED_GAP_KINDS,
    gap_intervals,
    in_closed_gap,
    session_link_events,
)
from canair.states.spans import parse_time_key

from .captures import CaptureWarning


def link_event_warnings(path: Path) -> list[CaptureWarning]:
    """Sessions in ``path`` with payload captures timed inside one of their gaps."""
    warnings: list[CaptureWarning] = []
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        events = session_link_events(session)
        if not events:
            continue
        inside = 0
        gaps_for: dict[tuple[str, str], list] = {}
        for cap in session.get("captures", []) or []:
            if not isinstance(cap, dict) or not cap.get("payload"):
                continue
            key = parse_time_key(cap.get("time"))
            if key is None:
                continue
            scope = (capture_io.capture_rx(cap), str(cap.get("pid", "")))
            gaps = gaps_for.get(scope)
            if gaps is None:
                gaps = gaps_for[scope] = gap_intervals(
                    events, *scope, kinds=ANSWER_BOUNDED_GAP_KINDS
                )
            if in_closed_gap(gaps, key):
                inside += 1
        if inside:
            warnings.append(
                CaptureWarning(
                    f"sessions[{si}]",
                    f"{inside} payload capture(s) timed inside a recorded gap — nothing "
                    "was gathered then, so the capture times or the link_events bounds "
                    "are wrong",
                )
            )
    return warnings
