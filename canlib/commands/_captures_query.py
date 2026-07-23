#!/usr/bin/env python3
"""Capture loading, query selection, and byte-diff/pair analysis helpers.

The pure data layer shared by the ``captures`` command's views (``captures.py``)
and its interactive step TUI (``_captures_step.py``): loading capture files,
resolving PID definitions, selecting entries with the query mini-language, and
the keying/grouping/pairing primitives the diff and step views build on.

Nothing here is interactive — functions return data (the one exception is
:func:`_gather_query`, which prints a note for selectors that matched nothing).
The ANSI colour constants live here as the single source of truth for the
``captures`` command family; the sibling modules import them from here.
"""

import sys
from datetime import datetime
from pathlib import Path

import yaml

from canlib.capture_dates import entry_datetime

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


def _decoded_preview(entry: dict) -> dict | None:
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
            from canlib.pids import build_ecu_index, load_pids

            _decode_fn = _decode_payload
            _ecu_index = build_ecu_index(load_pids())
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


def _entry_to_dict(e: dict, *, decoded: bool = True) -> dict:
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


def load_all_captures(captures_dir: Path | None = None) -> list[dict]:
    """Load all capture files and return a flat list of (session, capture) tuples.

    Each entry is a dict with keys:
        file, date, label, state, ecu, ecu_addr, pid, payload, response,
        scan_results, notes, time

    The capture ``ecu`` field stores the ECU CAN response address (e.g.
    ``"0x7EC"``); it is resolved to the canonical short name in ``ecu`` for
    display/joins, with the raw address preserved in ``ecu_addr``.

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

    entries = []
    for fpath in sorted(captures_dir.glob("*.yaml")):
        if fpath.name.startswith(("SCHEMA", "_")):
            continue
        with open(fpath) as f:
            data = yaml.safe_load(f)
        if not data or "sessions" not in data:
            continue
        for s_idx, session in enumerate(data["sessions"]):
            date = session.get("date", "")
            label = session.get("label", "")
            vehicle_states = session.get("vehicle_states") or []
            session_notes = session.get("notes", "")
            for c_idx, cap in enumerate(session.get("captures", [])):
                raw_ecu = cap.get("ecu", "")
                entry = {
                    "file": fpath.name,
                    "date": date,
                    "session_label": label,
                    "vehicle_states": list(vehicle_states),
                    "session_notes": session_notes,
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

    Parameters come from an *exact* PID key match (substring-matched captures
    with no exact definition render as raw hex, i.e. empty parameters).
    """
    info = ecu_index.get(str(ecu).upper())
    if not info:
        return {}, None
    tx_id = info.get("tx_id")
    pid_info = info.get("pids", {}).get(str(pid).upper())
    parameters = (pid_info or {}).get("parameters", {}) or {}
    return parameters, tx_id


def _gather_query(
    entries: list[dict], query, *, warn: bool = True
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
        known_ecus = {e["ecu"].upper() for e in payloads}
        for sel in empty:
            hint = ""
            # Bare selector whose "ECU" isn't a real ECU but looks like a DID —
            # likely the old `ECU PID` space form; nudge toward `ECU:PID`.
            if not sel.pids and sel.ecu not in known_ecus and any(c.isdigit() for c in sel.ecu):
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


def _capture_key(e: dict) -> tuple[str, str]:
    """The (ECU, PID) grouping/diff key for a capture (upper-cased)."""
    return e["ecu"].upper(), str(e["pid"]).upper()


def _dedupe_payloads(payloads: list[dict]) -> list[dict]:
    """Drop duplicate payloads per (ECU, PID), keeping first-seen order.

    Deduping is scoped to each ECU+PID so identical hex under different PIDs is
    never collapsed together.
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict] = []
    for e in payloads:
        ecu, pid = _capture_key(e)
        norm = e["payload"].upper().replace(" ", "")
        key = (ecu, pid, norm)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def _prev_same_index(captures: list[dict]) -> list[int | None]:
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


def _key_ordinals(captures: list[dict]) -> list[tuple[int, int]]:
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


def _pair_by_time(
    a_indices: list[int],
    b_indices: list[int],
    dts: dict[int, datetime],
    tol_s: float,
) -> list[tuple[int | None, int | None]]:
    """Merge two time-sorted index lists into ``(left, right)`` pair frames.

    ``a_indices``/``b_indices`` are indices into a shared captures list, each
    pre-sorted by its timestamp (looked up in ``dts``). Two captures whose
    timestamps fall within ``tol_s`` seconds are paired into one frame; captures
    with no counterpart in range appear alone (the other side is ``None``), so
    the merged timeline hides nothing. The tolerance follows the
    nearest-within-window join semantics of :mod:`canlib.align`.
    """
    frames: list[tuple[int | None, int | None]] = []
    ia = ib = 0
    na, nb = len(a_indices), len(b_indices)
    while ia < na and ib < nb:
        ta, tb = dts[a_indices[ia]], dts[b_indices[ib]]
        if abs((ta - tb).total_seconds()) <= tol_s:
            frames.append((a_indices[ia], b_indices[ib]))
            ia += 1
            ib += 1
        elif ta < tb:
            frames.append((a_indices[ia], None))
            ia += 1
        else:
            frames.append((None, b_indices[ib]))
            ib += 1
    frames.extend((a_indices[ia + k], None) for k in range(na - ia))
    frames.extend((None, b_indices[ib + k]) for k in range(nb - ib))
    return frames


def _build_pair_frames(
    captures: list[dict], tol_s: float
) -> tuple[list[tuple[int | None, int | None]], tuple[str, str], tuple[str, str], int]:
    """Build the two-ECU pair timeline from captures spanning exactly two keys.

    Returns ``(frames, key_a, key_b, n_no_time)`` where ``key_a``/``key_b`` are
    the two (ECU, PID) keys in first-appearance order and ``n_no_time`` counts
    captures excluded from pairing for lacking a usable timestamp (as in
    :mod:`canlib.align`). The caller must have already verified there are exactly
    two distinct keys.
    """
    keys = list(_group_by_key(captures))
    key_a, key_b = keys[0], keys[1]
    a_indices: list[int] = []
    b_indices: list[int] = []
    dts: dict[int, datetime] = {}
    n_no_time = 0
    for idx, e in enumerate(captures):
        dt = entry_datetime(e)
        if dt is None:
            n_no_time += 1
            continue
        dts[idx] = dt
        (a_indices if _capture_key(e) == key_a else b_indices).append(idx)
    a_indices.sort(key=lambda i: dts[i])
    b_indices.sort(key=lambda i: dts[i])
    return _pair_by_time(a_indices, b_indices, dts, tol_s), key_a, key_b, n_no_time
