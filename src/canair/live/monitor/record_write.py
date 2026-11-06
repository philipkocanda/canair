"""Live monitor — the functions that write a recording: merge, save and open the journal.

:class:`~canair.live.monitor.record.MonitorRecorder` and ``mode_monitor`` share these, so a
segment rotation and a run start open their journal the same way.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from canair.captures.journal import CaptureJournal
from canair.captures.keepmode import KeepMode, persisted_keep_mode
from canair.captures.types import Quality
from canair.profile import Profile

from ...captures.build import build_query_session
from ...captures.save import save_session


def merge_history(
    hex_history: dict[tuple[str, str], list[tuple[str, str]]],
    prev_hex: dict[tuple[str, str], str],
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Merge the latest ``prev_hex`` snapshot into the payload history.

    Returns a ``{(ecu_label, pid): [(hex, timestamp), ...]}`` map. A PID whose
    current payload isn't already the last history entry gets it appended with a
    fresh timestamp, so a bare snapshot (no history kept) still yields one row
    per PID.
    """
    all_keys = set(hex_history.keys()) | set(prev_hex.keys())
    merged: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key in all_keys:
        entries = list(hex_history.get(key, []))
        cur = prev_hex.get(key, "")
        if cur and cur not in [h for h, _ts in entries]:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            entries.append((cur, ts))
        if entries:
            merged[key] = entries
    return merged


def write_merged(
    merged: dict[tuple[str, str], list[tuple[str, str]]],
    label: str,
    vehicle_states,
    notes: str,
    captures_dir: Path,
    keep_mode: KeepMode | None = None,
    transport: str | None = None,
    quality: Quality | None = None,
    *,
    profile: Profile,
) -> Path:
    """Build a query-capture session from merged payloads and save it to disk.

    The ECU label (e.g. "BMS") is resolved to its CAN response address; an
    unknown label falls back to its leading token verbatim.
    """
    from ...ecus.names import build_name_tx_index, rx_from_name

    name_index = build_name_tx_index(profile=profile)
    results: list[tuple[str, str, str, str]] = []
    for (ecu_label, pid), entries in sorted(merged.items()):
        m = re.match(r"(\w+)", ecu_label)
        assert m is not None  # ecu_label is an ECU name/label, always starts with a word char
        ecu_short = m.group(1)
        ecu_ref = rx_from_name(ecu_short, name_index, profile=profile) or ecu_short
        for hex_val, ts in entries:
            results.append((ecu_ref, pid, hex_val, ts))

    session = build_query_session(
        results,
        label,
        vehicle_states,
        notes,
        keep_mode=keep_mode,
        transport=transport,
        quality=quality,
    )
    return save_session(session, captures_dir, profile=profile)


def open_journal(controller, label: str | None, vehicle_states, notes: str | None):
    """Open a write-ahead capture journal for a monitor --save run/segment.

    Shared by ``mode_monitor`` (run start) and ``MonitorRecorder.new_segment``
    (segment rotate) so their open args can't drift. The recording keep-mode
    (``changes``) is carried through from the controller; ``all``/``last`` keep
    every row.
    The transport label (for saved-capture provenance) comes from the controller.
    """
    assert controller.captures_dir is not None  # set by mode_monitor before any recording
    journal_label = label or controller.view.query_label() or "Monitor session"
    keep = persisted_keep_mode(controller.keep_mode)
    return CaptureJournal.open(
        controller.captures_dir,
        label=journal_label,
        vehicle_states=list(vehicle_states or []),
        notes=notes,
        source="monitor",
        keep_mode=keep,
        transport=getattr(controller, "transport_type", None),
        profile=controller.profile,
    )
