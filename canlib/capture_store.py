"""Reading the capture store: load the entries, resolve definitions, decode a value.

The data layer under every capture-consuming surface. It lives in the library, not
beside the ``captures`` command, because non-command modules need it too:
:mod:`canlib.align` and :mod:`canlib.capture_dates` load entries, and
:mod:`canlib.state_infer` resolves PID definitions. Those previously imported *up*
into ``commands/captures/`` — a layering inversion the lazy in-function imports
were working around.

Three concerns, in dependency order:

* :func:`load_all_captures` — flatten every capture file into one list of rows,
  with the stored RX address resolved to its canonical ECU short name.
* :func:`load_pid_captures` — the same, narrowed to one ECU+PID and reshaped to
  the slim rows the value-centric analysis views consume.
* :func:`load_ecu_index` / :func:`resolve_pid_defs` — look up a PID's parameters
  and its ECU's TX id.
* :func:`decoded_preview` — regenerate a capture's decoded parameter values.

Presentation (colour, JSON shaping, the QUERY mini-language) stays in the command
layer: see :mod:`canlib.commands.captures.query`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from canlib import capture_io, captures_merge
from canlib.capture_types import CaptureEntry
from canlib.state_spans import span_keys, states_at

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


def decoded_preview(entry: Mapping[str, Any]) -> dict | None:
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


# ---------------------------------------------------------------------------
# Per-capture state resolution
# ---------------------------------------------------------------------------


def session_spans(session: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The ``state_spans.spans`` list of a session, or ``[]``.

    Tolerant accessor in the spirit of :func:`capture_io.capture_rx`, so callers
    never reach through the nested provenance wrapper themselves. A malformed
    block (hand-edited, or a shape from a future version) yields no spans rather
    than raising — the ladder in :func:`resolve_capture_states` then degrades to
    the session union and flags the row.
    """
    block = session.get("state_spans")
    if not isinstance(block, Mapping):
        return []
    spans = block.get("spans")
    if not isinstance(spans, list):
        return []
    return [s for s in spans if isinstance(s, Mapping)]


def resolve_capture_states(
    session_states: Sequence[str],
    spans: Sequence[Mapping[str, Any]],
    keys: Sequence[float] | None,
    when: Any,
) -> tuple[list[str], bool]:
    """One capture's states, as ``(states, resolved)``.

    The single seam that makes every state-aware analysis command temporally
    correct without changing any of them. A session's ``vehicle_states`` is a
    union over the whole recording, so using it per capture reports a drive's
    bytes as charging data; spans narrow it to the instant. The ladder:

    1. Spans cover this capture's timestamp → exact. An **empty** result is exact
       too ("nothing matched here"), so it is returned rather than falling
       through as if the lookup had missed.
    2. The session has at most one state → the union already *is* the per-capture
       truth. Exact.
    3. Otherwise (multi-state with no spans, or an untimed capture) → the union,
       flagged ``resolved=False``. The imprecision is reported, never assumed
       away: see :func:`unresolved_state_summary`.
    """
    if spans:
        at = states_at(spans, when, keys)
        if at is not None:
            return at, True
    states = [str(s) for s in session_states]
    return states, len(states) <= 1


def unresolved_state_summary(entries: Sequence[CaptureEntry]) -> tuple[int, int]:
    """``(captures, sessions)`` whose states could not be resolved to an instant.

    Feeds the one-line provenance note a state-grouping or state-filtering view
    prints, in the spirit of :mod:`canlib.fill`'s reported forward fills: a
    span-less multi-state session degrades *loudly*.
    """
    sessions: set[tuple[str, int]] = set()
    n = 0
    for e in entries:
        if e.get("states_resolved", True):
            continue
        n += 1
        sessions.add((e.get("file", ""), e.get("_session_idx", 0)))
    return n, len(sessions)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def flatten_session(
    path: Path,
    session_idx: int,
    session: Mapping[str, Any],
    rx_index: dict[int, str] | None = None,
) -> list[CaptureEntry]:
    """Flatten one session into per-capture entries, resolving each row's states.

    The unit of :func:`load_all_captures`, split out so a caller holding a single
    session (a save-time hook annotating what it just wrote) can resolve it
    without paying for the whole store.

    Takes the source file's ``path`` rather than its name so each row can carry
    both the display ``file`` and the ``_path`` a mutation reopens.
    """
    from canlib.ecus import ecu_name_from_ref

    if rx_index is None:
        rx_index = _rx_index()

    date = session.get("date", "")
    label = session.get("label", "")
    version = session.get("version", "")
    vehicle_states = session.get("vehicle_states") or []
    spans = session_spans(session)
    keys = span_keys(spans) if spans else None
    session_notes = session.get("notes", "")
    keep_mode = session.get("keep_mode", "")
    transport = session.get("transport", "")
    quality = session.get("quality") or None

    entries: list[CaptureEntry] = []
    for c_idx, cap in enumerate(session.get("captures", [])):
        raw_ecu = capture_io.capture_rx(cap)
        cap_time = cap.get("time", "")
        states, resolved = resolve_capture_states(vehicle_states, spans, keys, cap_time)
        entries.append(
            {
                "file": path.name,
                "date": date,
                "session_label": label,
                "session_version": version,
                "vehicle_states": states,
                "session_states": list(vehicle_states),
                "states_resolved": resolved,
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
                "time": cap_time,
                "label": cap.get("label", ""),
                "_path": str(path),
                "_session_idx": session_idx,
                "_capture_idx": c_idx,
            }
        )
    return entries


def _rx_index() -> dict[int, str]:
    """The rx-address → ECU-name index, tolerating an unreadable registry."""
    from canlib.ecus import build_rx_index

    try:
        return build_rx_index()
    except Exception:
        return {}


def entry_path(entry: Mapping[str, Any], captures_dir: Path | None = None) -> Path:
    """The capture file a mutation must reopen for ``entry``.

    Rows from :func:`load_all_captures` carry the resolved ``_path``; ``file`` is
    only the display name, so joining it onto a captures dir guesses at the owning
    directory and guesses wrong as soon as reads span more than one (a layered
    profile, or a caller passing an explicit ``captures_dir``). ``captures_dir`` is
    the fallback for a hand-built row that has no locator.
    """
    located = entry.get("_path")
    if located:
        return Path(located)
    return capture_io.resolve_captures_dir(captures_dir) / entry["file"]


def load_all_captures(captures_dir: Path | None = None) -> list[CaptureEntry]:
    """Load all capture files and return a flat list of (session, capture) tuples.

    Each entry is a dict with keys:
        file, date, session_label, session_version, vehicle_states,
        session_states, states_resolved, session_notes, keep_mode, transport,
        quality, ecu, ecu_addr, pid, payload, response, scan_results, notes,
        time, label

    ``vehicle_states`` is the capture's states **resolved to its own timestamp**
    through the session's ``state_spans`` (:func:`resolve_capture_states`), which
    is what makes ``--state CHARGING`` mean "sampled while charging" rather than
    "from a session that charged at some point". ``session_states`` keeps the
    session-level union for session views, and ``states_resolved`` is False when
    the row fell back to that union.

    On disk the capture stores the ECU CAN *response* address under ``rx``
    (e.g. ``"0x7EC"``; read tolerantly via :func:`capture_io.capture_rx`, which
    also accepts the legacy ``ecu`` key). It is resolved to the canonical short
    name in the entry's ``ecu`` field for display/joins, with the raw address
    preserved in ``ecu_addr``.

    Plus internal locator keys (``_path``, ``_session_idx``, ``_capture_idx``) that
    address the capture within its source file, for in-place edits/deletes.

    A **layered** profile is read across every layer (base first, then the user's
    overlay). Layers are not merged into synthetic documents: each session is
    flattened from the file it actually lives in, so the locators stay true to disk
    and a mutation reopens the right file. A session present in both layers (a
    ``git pull`` bringing home what you already had) is kept once, keyed by content
    via :func:`captures_merge.session_key`.
    """
    rx_index = _rx_index()
    layers = capture_io.resolve_capture_layers(captures_dir)

    entries: list[CaptureEntry] = []
    seen: set[str] = set()
    for layer in layers:
        if not layer.is_dir():
            continue
        capture_io.ensure_migrated(layer)
        for fpath in capture_io.iter_capture_files(layer):
            data = capture_io.load_capture_file(fpath)
            if not data or "sessions" not in data:
                continue
            for s_idx, session in enumerate(data["sessions"]):
                key = captures_merge.session_key(session)
                if key in seen:
                    continue
                seen.add(key)
                entries.extend(flatten_session(fpath, s_idx, session, rx_index))
    if len(layers) > 1:
        # Within one store, file order already is chronological; across layers it
        # is not, and a stable sort leaves the single-layer result untouched.
        entries.sort(key=lambda e: (e.get("date") or "", e.get("time") or ""))
    return entries


def load_pid_captures(ecu: str, pid: str) -> list[dict]:
    """Every payload capture for one ECU+PID, as the slim rows the analysis verbs use.

    Narrows :func:`load_all_captures` to a single ECU+PID (both matched
    case-insensitively, with the stored RX address already resolved to its
    canonical short name) and reshapes each row to the keys the value-centric
    views and the ``capture_dates`` scope filters read: ``file``, ``date``,
    ``label`` (the *session* label), ``vehicle_states`` (resolved per capture),
    ``session_states``, ``states_resolved``, ``payload``, ``notes``, ``time``,
    ``keep_mode``. Captures with no payload are skipped.
    """
    entries: list[dict] = []
    for e in load_all_captures():
        if str(e.get("ecu", "")).upper() != ecu.upper():
            continue
        if str(e.get("pid", "")).upper() != pid.upper():
            continue
        if not e.get("payload"):
            continue
        entries.append(
            {
                "file": e.get("file", ""),
                "date": str(e.get("date", "")),
                "label": e.get("session_label", ""),
                "vehicle_states": list(e.get("vehicle_states") or []),
                "session_states": list(e.get("session_states") or []),
                "states_resolved": e.get("states_resolved", True),
                "payload": e["payload"],
                "notes": e.get("notes", ""),
                "time": e.get("time", ""),
                "keep_mode": e.get("keep_mode", ""),
                "_session_idx": e.get("_session_idx", 0),
            }
        )
    return entries


# ---------------------------------------------------------------------------
# PID definition resolution
# ---------------------------------------------------------------------------

# A resolved PID definition: (parameters, tx_id) for one (ECU, PID) pair.
PidDefs = tuple[dict, "int | None"]


def load_ecu_index() -> dict:
    """Load + build the ECU/PID definition index once (empty dict on failure)."""
    try:
        from canlib.pids import build_ecu_index, load_pids

        return build_ecu_index(load_pids())
    except Exception:
        return {}


def resolve_pid_defs(ecu_index: dict, ecu: str, pid: str) -> PidDefs:
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
