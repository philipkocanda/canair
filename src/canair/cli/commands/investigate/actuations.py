"""What ``investigate --events`` says the recording switched, between the edges it shows.

A ``monitor --save --actuate`` session records each request its actuator pane sent
(:mod:`canair.captures.actuations`). The edge timeline prints them in time order among the
edges, so a byte that flips right after ``IGPM BC0C on`` is read against the toggle.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from canair.captures.actuations import Actuation, actuations_in, describe
from canair.cli.profile_context import active
from canair.ecus.registry import build_rx_index, ecu_name_from_ref
from canair.render import ansi


def actuation_marks(captures: Sequence[Any]) -> list[tuple[datetime, str, Actuation]]:
    """``(when, ECU name, entry)`` for every actuation of the sessions ``captures`` are from."""
    pairs = actuations_in(captures)
    if not pairs:
        return []
    profile = active()
    rx_index = build_rx_index(profile=profile)
    return [(when, ecu_name_from_ref(a["rx"], rx_index, profile=profile), a) for when, a in pairs]


def mark_line(when: datetime, ecu: str, entry: Actuation) -> str:
    """One timeline row: ``14:02:11  switched IGPM iocontrol BC0C on (ok)``."""
    return (
        f"    {ansi.DIM}{when.strftime('%H:%M:%S')}{ansi.RESET}  {ansi.BOLD}switched{ansi.RESET} "
        f"{ansi.CYAN}{ecu}{ansi.RESET} {describe(entry)}"
    )


def marks_json(marks: Sequence[tuple[datetime, str, Actuation]]) -> list[dict]:
    return [{"time": when.strftime("%H:%M:%S"), "ecu": ecu, **entry} for when, ecu, entry in marks]
