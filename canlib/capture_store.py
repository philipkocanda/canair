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

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from canlib import capture_io
from canlib.capture_types import CaptureEntry

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
        captures_dir = capture_io.resolve_captures_dir()

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


def load_pid_captures(ecu: str, pid: str) -> list[dict]:
    """Every payload capture for one ECU+PID, as the slim rows the analysis verbs use.

    Narrows :func:`load_all_captures` to a single ECU+PID (both matched
    case-insensitively, with the stored RX address already resolved to its
    canonical short name) and reshapes each row to the keys the value-centric
    views and the ``capture_dates`` scope filters read: ``file``, ``date``,
    ``label`` (the *session* label), ``vehicle_states``, ``payload``, ``notes``,
    ``time``, ``keep_mode``. Captures with no payload are skipped.
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
                "payload": e["payload"],
                "notes": e.get("notes", ""),
                "time": e.get("time", ""),
                "keep_mode": e.get("keep_mode", ""),
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
