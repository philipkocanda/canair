"""Helpers over flat capture entries: JSON shaping, keying, dedup, and grouping into sessions.

The data layer the ``captures`` command's views and the capture stepper
(:mod:`canair.tui.apps.captures_step.app`) share. An entry is a row of
:func:`canair.captures.store.load_all_captures`; nothing here reads files or prints.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from canair.captures.keepmode import EntryKeepMode
from canair.captures.link_events import LinkEvent
from canair.captures.store import decoded_preview, entry_path
from canair.captures.types import CaptureEntry, Quality


def entry_to_dict(e: Mapping[str, Any], *, decoded: bool = True) -> dict:
    """Serialize a capture entry to a clean, JSON-ready dict.

    Includes the regenerated ``decoded`` preview (param -> formatted value) when
    ``decoded`` is set and a PID definition exists.
    """
    d = {
        "ecu": e.get("ecu"),
        "ecu_addr": e.get("ecu_addr"),
        "pid": str(e["pid"]) if e.get("pid") is not None else None,
        "date": e.get("date"),
        "time": e.get("time") or None,
        "vehicle_states": e.get("vehicle_states") or None,
        "label": e.get("label") or e.get("session_label") or None,
        "notes": (str(e["notes"]).strip() or None) if e.get("notes") else None,
        "payload": e.get("payload") or None,
        "response": e.get("response") or None,
        "scan_results": e.get("scan_results") or None,
    }
    if decoded:
        d["decoded"] = decoded_preview(e)
    return d


def is_hex_payload(payload) -> bool:
    """True if ``payload`` is a byte-diffable hex string.

    The byte-level views (``--diff``/``--step``) render payloads as hex. Some
    legacy captures store a human outcome (e.g. ``"NO DATA"``) under ``payload``
    instead of ``response``; those aren't hex and must be excluded here so the
    hex renderer never chokes on them. Spaces are tolerated (payloads are
    normally stored space-free, uppercase).
    """
    if not payload:
        return False
    s = str(payload).replace(" ", "")
    if not s or len(s) % 2 != 0:
        return False
    try:
        bytes.fromhex(s)
    except ValueError:
        return False
    return True


def capture_key(e: Mapping[str, Any]) -> tuple[str, str]:
    """The (ECU, PID) grouping/diff key for a capture (upper-cased)."""
    return e["ecu"].upper(), str(e["pid"]).upper()


def dedupe_payloads[R: Mapping[str, Any]](payloads: Sequence[R]) -> list[R]:
    """Drop duplicate payloads per (ECU, PID), keeping first-seen order.

    Deduping is scoped to each ECU+PID so identical hex under different PIDs is
    never collapsed together.
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[R] = []
    for e in payloads:
        ecu, pid = capture_key(e)
        norm = e["payload"].upper().replace(" ", "")
        key = (ecu, pid, norm)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def prev_same_index(captures: Sequence[Mapping[str, Any]]) -> list[int | None]:
    """Per position, the nearest earlier index sharing the same (ECU, PID).

    Used by the interleaved step view so byte-diffing compares a capture against
    the previous capture *of the same PID*, not merely the adjacent frame.
    """
    last: dict[tuple[str, str], int] = {}
    out: list[int | None] = []
    for idx, e in enumerate(captures):
        key = capture_key(e)
        out.append(last.get(key))
        last[key] = idx
    return out


def key_ordinals(captures: Sequence[Mapping[str, Any]]) -> list[tuple[int, int]]:
    """Per position, its 1-based ordinal within its (ECU, PID) and that group's total."""
    totals: dict[tuple[str, str], int] = {}
    for e in captures:
        totals[capture_key(e)] = totals.get(capture_key(e), 0) + 1
    seen: dict[tuple[str, str], int] = {}
    out: list[tuple[int, int]] = []
    for e in captures:
        key = capture_key(e)
        seen[key] = seen.get(key, 0) + 1
        out.append((seen[key], totals[key]))
    return out


def group_by_key(captures: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Group captures by (ECU, PID), preserving first-appearance order of keys."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for e in captures:
        groups.setdefault(capture_key(e), []).append(e)
    return groups


def key_index[R: Mapping[str, Any]](entries: Sequence[R]) -> dict[tuple[str, str], list[R]]:
    """Group payload-bearing ``entries`` by (ECU, PID), for repeated re-selection.

    The step TUI lets the user add/remove PIDs live, which re-selects captures on
    every change. Grouping the whole scoped set once up front makes each rebuild
    proportional to the *selected* keys instead of rescanning every loaded
    capture. Non-hex payloads are excluded, matching :func:`_gather_query`.
    """
    index: dict[tuple[str, str], list[R]] = {}
    for e in entries:
        if not is_hex_payload(e.get("payload")):
            continue
        index.setdefault(capture_key(e), []).append(e)
    return index


# ---------------------------------------------------------------------------
# Session grouping
# ---------------------------------------------------------------------------


class SessionGroup(TypedDict):
    """Rolled-up per-session accumulator built by :func:`group_sessions`."""

    file: str  # display name
    path: str  # the file a mutation reopens (see capture_store.entry_path)
    session_idx: int
    date: str
    label: str
    version: str
    vehicle_states: list  # the session-level union, not a per-capture resolution
    notes: str
    keep_mode: EntryKeepMode
    transport: str
    quality: Quality | None
    link_events: list[LinkEvent]
    variant: str  # "" when untagged
    n: int
    ecus: dict  # ordered set (dict) of ECU names
    times: list
    cap_notes: list  # distinct capture-level notes, first-seen order
    noted: list  # every capture entry carrying a note, in file order


def group_sessions(entries: Sequence[CaptureEntry]) -> list[SessionGroup]:
    """Reconstruct per-session metadata from flat capture entries.

    Groups by ``(_path, _session_idx)`` — the true session identity — and rolls
    up each session's date, label, state, session-level notes, capture count,
    the distinct ECUs touched, the time span, any distinct capture-level notes,
    and the noted capture entries themselves (which carry the locators a jump
    target needs). Sessions are returned in chronological order (date, then
    first time).
    """
    groups: dict[tuple[str, int], SessionGroup] = {}
    for e in entries:
        path = str(entry_path(e))
        key = (path, e.get("_session_idx", 0))
        g: SessionGroup | None = groups.get(key)
        if g is None:
            g = {
                "file": e["file"],
                "path": path,
                "session_idx": e.get("_session_idx", 0),
                "date": e.get("date", ""),
                "label": e.get("session_label", ""),
                "version": e.get("session_version", ""),
                # The session's union, never a row's resolved states: a session
                # view answers "what did this recording cover?". Falls back to the
                # row's own states for a hand-built entry (tests, external
                # callers) that predates the per-capture resolution.
                "vehicle_states": e.get("session_states") or e.get("vehicle_states") or [],
                "notes": e.get("session_notes", ""),
                "keep_mode": e.get("keep_mode", ""),
                "transport": e.get("transport", ""),
                "quality": e.get("quality") or None,
                "link_events": list(e.get("session_link_events") or []),
                "variant": e.get("session_variant", ""),
                "n": 0,
                "ecus": {},  # ordered set (dict) of ECU names
                "times": [],
                "cap_notes": [],  # distinct capture-level notes, first-seen order
                "noted": [],
            }
            groups[key] = g
        g["n"] += 1
        ecu = e.get("ecu") or e.get("ecu_addr") or ""
        if ecu:
            g["ecus"].setdefault(ecu, None)
        t = str(e.get("time", "")).strip()
        if t:
            g["times"].append(t)
        cn = str(e.get("notes", "")).strip()
        if cn:
            g["noted"].append(e)
            if cn not in g["cap_notes"]:
                g["cap_notes"].append(cn)

    sessions = list(groups.values())
    sessions.sort(key=lambda g: (str(g["date"]), min(g["times"]) if g["times"] else ""))
    return sessions
