"""``canair dtc --save``: keep the snapshot replies a read fetched as captures.

Each reply is stored under its own request (``1904582C0001``), never under the DID's read
PID (``224710``): a snapshot is what the ECU recorded when the fault was set, so it must
never pass for a live sample of the DID. Its time is when it was *read*, which the
session notes say.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from canair.captures.build import build_query_session
from canair.captures.oneshot import save_session_journaled
from canair.ecus.registry import rx_addr_str
from canair.profile.model import Profile
from canair.transport.stats import client_transport

SESSION_NOTE = (
    "DTC snapshot replies (19 04), stored under their request. Each time is when the reply "
    "was read, not when the fault was set."
)


def snapshot_rows(
    results: Iterable[Mapping[str, Any]], *, profile: Profile
) -> list[tuple[str, str, str, str]]:
    """``(rx, request, payload, time)`` for every snapshot reply in ``results``."""

    rows = []
    for result in results:
        rx = rx_addr_str(int(result["tx"], 16), profile=profile)
        for code in result.get("dtcs") or ():
            for snap in code.get("snapshots") or ():
                # A reply holding a sensitive DID is never written (canair.privacy.sensitive).
                if snap.get("hex") and snap.get("request") and not snap.get("sensitive"):
                    rows.append((rx, snap["request"], snap["hex"], snap.get("time", "")))
    return rows


def save_snapshot_captures(
    results: Iterable[Mapping[str, Any]],
    *,
    label: str | None,
    vehicle_states: list | None,
    notes: str | None,
    transport: str | None,
    profile: Profile,
) -> Path | None:
    """Save the snapshot replies as one capture session; None when there were none."""
    rows = snapshot_rows(results, profile=profile)
    if not rows:
        return None
    session_notes = f"{notes} — {SESSION_NOTE}" if notes else SESSION_NOTE
    session = build_query_session(
        rows,
        label or "DTC snapshots",
        list(vehicle_states or []),
        session_notes,
        transport=transport,
    )
    return save_session_journaled(session, profile=profile)


def report_saved(
    terminal, results, label, vehicle_states, notes, as_json: bool, *, profile: Profile
) -> None:
    """Save the snapshot replies in ``results`` and say where they landed (on stderr)."""
    import sys

    path = save_snapshot_captures(
        results,
        label=label,
        vehicle_states=vehicle_states,
        notes=notes,
        transport=client_transport(terminal),
        profile=profile,
    )
    if path is None:
        print("  --save: no snapshot replies to save.", file=sys.stderr)
