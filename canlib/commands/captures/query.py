#!/usr/bin/env python3
"""Capture loading, query selection, and capture-set keying helpers.

The pure data layer shared by the ``captures`` command's views (``captures.py``)
and its interactive step TUI (``step_model.py``): loading capture
files, resolving PID definitions, selecting entries with the query
mini-language, and the keying/grouping/dedup primitives the diff and step views
build on. The N-way time join those frames are built from lives in
:mod:`join`.

Nothing here is interactive — functions return data (the one exception is
:func:`_gather_query`, which prints a note for selectors that matched nothing).
The ANSI colour constants live here as the single source of truth for the
``captures`` command family; the sibling modules import them from here.
"""

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

from canlib import capture_io
from canlib.capture_types import CaptureEntry, Quality
from canlib.keepmode import EntryKeepMode

# ANSI color helpers (shared across the captures command family).
_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


# ---------------------------------------------------------------------------
# On-the-fly decoding
# ---------------------------------------------------------------------------
#
# Decoded parameter values are NOT stored in capture files (they are derived
# data). We regenerate them on demand from the payload + PID definitions when
# displaying previews. The PID index is built once and cached.

_ecu_index = None
_decode_fn = None


def _clear_decode_index() -> None:
    """Drop the PID index (registered with canlib.pids.clear_cache).

    Without this, a profile switch or an ECU edit leaves previews decoding with
    the previous definitions — reporting the wrong profile's parameter names.
    """
    global _ecu_index, _decode_fn
    _ecu_index = None
    _decode_fn = None


def _decoded_preview(entry: Mapping[str, Any]) -> dict | None:
    """Regenerate decoded parameter values for a capture entry, or None.

    Lazily loads PID definitions on first use. Returns a dict of
    ``param_name -> "value unit (formatted)"`` strings, matching the format
    previously stored in the (now removed) ``decoded`` field.
    """
    global _ecu_index, _decode_fn

    payload = entry.get("payload")
    ecu = entry.get("ecu")
    pid = entry.get("pid")
    if not payload or not ecu or not pid:
        return None

    if _decode_fn is None:
        try:
            from canlib.captures import _decode_payload
            from canlib.pids import build_ecu_index, load_pids, register_derived_cache

            _decode_fn = _decode_payload
            _ecu_index = build_ecu_index(load_pids())
            register_derived_cache(_clear_decode_index)
        except Exception:
            _decode_fn = False  # sentinel: decoding unavailable
            return None
    if _decode_fn is False:
        return None

    try:
        return _decode_fn(ecu, str(pid), payload, {}, ecu_index=_ecu_index)
    except Exception:
        return None


def _dump_json(obj) -> None:
    """Write ``obj`` to stdout as pretty JSON (dates/other objects via str())."""
    import json

    json.dump(obj, sys.stdout, indent=2, default=str)
    print()


def _entry_to_dict(e: Mapping[str, Any], *, decoded: bool = True) -> dict:
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
        d["decoded"] = _decoded_preview(e)
    return d


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_all_captures(captures_dir: Path | None = None) -> list[CaptureEntry]:
    """Load all capture files and return a flat list of (session, capture) tuples.

    Each entry is a dict with keys:
        file, date, label, state, ecu, ecu_addr, pid, payload, response,
        scan_results, notes, time

    On disk the capture stores the ECU CAN *response* address under ``rx``
    (e.g. ``"0x7EC"``; read tolerantly via :func:`capture_io.capture_rx`, which
    also accepts the legacy ``ecu`` key). It is resolved to the canonical short
    name in the entry's ``ecu`` field for display/joins, with the raw address
    preserved in ``ecu_addr``.

    Plus internal locator keys (``_session_idx``, ``_capture_idx``) that address
    the capture within its source file, for in-place edits/deletes.
    """
    from canlib.ecus import build_rx_index, ecu_name_from_ref

    if captures_dir is None:
        from canlib.profile import active

        captures_dir = active().captures_dir

    try:
        rx_index = build_rx_index()
    except Exception:
        rx_index = {}

    entries: list[CaptureEntry] = []
    capture_io.ensure_migrated(captures_dir)
    for fpath in capture_io.iter_capture_files(captures_dir):
        data = capture_io.load_capture_file(fpath)
        if not data or "sessions" not in data:
            continue
        for s_idx, session in enumerate(data["sessions"]):
            date = session.get("date", "")
            label = session.get("label", "")
            version = session.get("version", "")
            vehicle_states = session.get("vehicle_states") or []
            session_notes = session.get("notes", "")
            keep_mode = session.get("keep_mode", "")
            transport = session.get("transport", "")
            quality = session.get("quality") or None
            for c_idx, cap in enumerate(session.get("captures", [])):
                raw_ecu = capture_io.capture_rx(cap)
                entry: CaptureEntry = {
                    "file": fpath.name,
                    "date": date,
                    "session_label": label,
                    "session_version": version,
                    "vehicle_states": list(vehicle_states),
                    "session_notes": session_notes,
                    "keep_mode": keep_mode,
                    "transport": transport,
                    "quality": quality,
                    "ecu": ecu_name_from_ref(raw_ecu, rx_index) if raw_ecu else "",
                    "ecu_addr": raw_ecu,
                    "pid": cap.get("pid", ""),
                    "payload": cap.get("payload"),
                    "response": cap.get("response"),
                    "scan_results": cap.get("scan_results"),
                    "notes": cap.get("notes", ""),
                    "time": cap.get("time", ""),
                    "label": cap.get("label", ""),
                    "_session_idx": s_idx,
                    "_capture_idx": c_idx,
                }
                entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Query parsing (alias-aware)
# ---------------------------------------------------------------------------


def build_query(tokens: list[str]) -> str:
    """Turn positional CLI tokens into a query string for ``canlib.query``.

    Two bare tokens (neither containing ``:``) collapse to the decode.py-style
    ``ECU PID`` form, i.e. ``ECU:PID`` — so ``BMS 2102`` becomes ``BMS:2102``.
    Everything else is space-joined and handed to the mini-language unchanged, so
    ``BMS:2102,2103`` and a quoted ``"VCU:2101 BMS:2101"`` pass straight through.
    """
    if not tokens:
        return ""
    if len(tokens) == 2 and ":" not in tokens[0] and ":" not in tokens[1]:
        return f"{tokens[0]}:{tokens[1]}"
    return " ".join(tokens)


def _parse_query(query):
    """Parse a QUERY and canonicalize selector ECUs (aliases -> primary name).

    So `canair captures SMK` resolves to the SKM module. Falls back to the raw
    parse if the ECU registry is unavailable; :class:`EcuNameCollision` from an
    ambiguous registry is allowed to propagate.
    """
    from canlib.query import parse_query

    q = parse_query(query)
    try:
        from canlib.ecus import build_canonical_name_index

        name_index = build_canonical_name_index()
    except FileNotFoundError:
        return q
    return q.canonicalize_ecus(lambda ecu: name_index.get(ecu, ecu).upper())


# ---------------------------------------------------------------------------
# Query gathering (shared by --diff and --step)
# ---------------------------------------------------------------------------

# A resolved PID definition: (parameters, tx_id) for one (ECU, PID) pair.
PidDefs = tuple[dict, "int | None"]


def _load_ecu_index() -> dict:
    """Load + build the ECU/PID definition index once (empty dict on failure)."""
    try:
        from canlib.pids import build_ecu_index, load_pids

        return build_ecu_index(load_pids())
    except Exception:
        return {}


def _resolve_defs(ecu_index: dict, ecu: str, pid: str) -> PidDefs:
    """Look up ``(parameters, tx_id)`` for one ECU+PID from the index.

    Parameters come from an *exact* PID key match (boundary-matched captures
    with no exact definition render as raw hex, i.e. empty parameters).
    """
    info = ecu_index.get(str(ecu).upper())
    if not info:
        return {}, None
    tx_id = info.get("tx_id")
    pid_info = info.get("pids", {}).get(str(pid).upper())
    parameters = (pid_info or {}).get("parameters", {}) or {}
    return parameters, tx_id


def _gather_query[R: Mapping[str, Any]](
    entries: Sequence[R], query, *, warn: bool = True
) -> tuple[list[dict], dict[tuple[str, str], PidDefs]]:
    """Select payload captures matching ``query`` (a canlib.query string/Query).

    Returns ``(captures, defs)``:
      - ``captures`` — payload-bearing entries matching any selector, sorted
        chronologically ``(date, time)``.
      - ``defs`` — cache mapping ``(ECU_UPPER, PID_UPPER)`` to ``(parameters,
        tx_id)`` for every distinct pair present in ``captures``.

    When ``warn`` is set, prints a note for any selector that matched nothing
    (with an ``ECU:PID`` hint when a bare selector looks like a DID).
    """
    q = _parse_query(query)
    payloads = [e for e in entries if _is_hex_payload(e.get("payload"))]
    matched, empty = q.filter(payloads, ecu_of=lambda e: e["ecu"], pid_of=lambda e: e["pid"])

    # Chronological order (date, then time within a session).
    matched.sort(key=lambda e: (str(e.get("date", "")), str(e.get("time", ""))))

    ecu_index = _load_ecu_index()
    defs: dict[tuple[str, str], PidDefs] = {}
    for e in matched:
        key = (e["ecu"].upper(), str(e["pid"]).upper())
        if key not in defs:
            defs[key] = _resolve_defs(ecu_index, *key)

    if warn and empty:
        from canlib.query import looks_like_pid

        known_ecus = {e["ecu"].upper() for e in payloads}
        for sel in empty:
            hint = ""
            # Bare selector whose "ECU" isn't a real ECU but looks like a DID —
            # likely the old `ECU PID` space form; nudge toward `ECU:PID`.
            if not sel.pids and sel.ecu not in known_ecus and looks_like_pid(sel.ecu):
                hint = "  (did you mean to attach it as a PID, e.g. ECU:PID?)"
            print(f"  {_YELLOW}No captures matched selector '{sel}'{_RESET}{hint}")
        avail = ", ".join(sorted(known_ecus))
        print(f"  {_DIM}Available ECUs: {avail}{_RESET}")

    return matched, defs


def _is_hex_payload(payload) -> bool:
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


def _capture_key(e: Mapping[str, Any]) -> tuple[str, str]:
    """The (ECU, PID) grouping/diff key for a capture (upper-cased)."""
    return e["ecu"].upper(), str(e["pid"]).upper()


def _dedupe_payloads[R: Mapping[str, Any]](payloads: Sequence[R]) -> list[R]:
    """Drop duplicate payloads per (ECU, PID), keeping first-seen order.

    Deduping is scoped to each ECU+PID so identical hex under different PIDs is
    never collapsed together.
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[R] = []
    for e in payloads:
        ecu, pid = _capture_key(e)
        norm = e["payload"].upper().replace(" ", "")
        key = (ecu, pid, norm)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def _prev_same_index(captures: Sequence[Mapping[str, Any]]) -> list[int | None]:
    """Per position, the nearest earlier index sharing the same (ECU, PID).

    Used by the interleaved step view so byte-diffing compares a capture against
    the previous capture *of the same PID*, not merely the adjacent frame.
    """
    last: dict[tuple[str, str], int] = {}
    out: list[int | None] = []
    for idx, e in enumerate(captures):
        key = _capture_key(e)
        out.append(last.get(key))
        last[key] = idx
    return out


def _key_ordinals(captures: Sequence[Mapping[str, Any]]) -> list[tuple[int, int]]:
    """Per position, its 1-based ordinal within its (ECU, PID) and that group's total."""
    totals: dict[tuple[str, str], int] = {}
    for e in captures:
        totals[_capture_key(e)] = totals.get(_capture_key(e), 0) + 1
    seen: dict[tuple[str, str], int] = {}
    out: list[tuple[int, int]] = []
    for e in captures:
        key = _capture_key(e)
        seen[key] = seen.get(key, 0) + 1
        out.append((seen[key], totals[key]))
    return out


def _group_by_key(captures: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Group captures by (ECU, PID), preserving first-appearance order of keys."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for e in captures:
        groups.setdefault(_capture_key(e), []).append(e)
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
        if not _is_hex_payload(e.get("payload")):
            continue
        index.setdefault(_capture_key(e), []).append(e)
    return index


# ---------------------------------------------------------------------------
# Session grouping
# ---------------------------------------------------------------------------


class SessionGroup(TypedDict):
    """Rolled-up per-session accumulator built by :func:`group_sessions`."""

    file: str
    session_idx: int
    date: str
    label: str
    version: str
    vehicle_states: list
    notes: str
    keep_mode: EntryKeepMode
    transport: str
    quality: Quality | None
    n: int
    ecus: dict  # ordered set (dict) of ECU names
    times: list
    cap_notes: list  # distinct capture-level notes, first-seen order
    noted: list  # every capture entry carrying a note, in file order


def group_sessions(entries: Sequence[CaptureEntry]) -> list[SessionGroup]:
    """Reconstruct per-session metadata from flat capture entries.

    Groups by ``(file, _session_idx)`` — the true session identity — and rolls
    up each session's date, label, state, session-level notes, capture count,
    the distinct ECUs touched, the time span, any distinct capture-level notes,
    and the noted capture entries themselves (which carry the locators a jump
    target needs). Sessions are returned in chronological order (date, then
    first time).
    """
    groups: dict[tuple[str, int], SessionGroup] = {}
    for e in entries:
        key = (e["file"], e.get("_session_idx", 0))
        g: SessionGroup | None = groups.get(key)
        if g is None:
            g = {
                "file": e["file"],
                "session_idx": e.get("_session_idx", 0),
                "date": e.get("date", ""),
                "label": e.get("session_label", ""),
                "version": e.get("session_version", ""),
                "vehicle_states": e.get("vehicle_states") or [],
                "notes": e.get("session_notes", ""),
                "keep_mode": e.get("keep_mode", ""),
                "transport": e.get("transport", ""),
                "quality": e.get("quality") or None,
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
