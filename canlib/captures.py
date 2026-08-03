"""Shared capture file save logic.

Provides functions for prompting session metadata and appending captures
to per-date YAML files in captures/. Used by scan, raw, discover, and
monitor modes.
"""

from datetime import datetime
from pathlib import Path
from typing import cast

from . import capture_io
from .capture_types import (
    CaptureFile,
    CaptureRecord,
    CaptureSession,
    RespondingEntry,
    ScanResults,
)
from .states import join_states as _join_states
from .states import parse_states as _parse_states
from .uds_parse import UdsResponse


def active_transport_label() -> str | None:
    """Best-effort label of the currently-configured transport (e.g. ``slcan-tcp``).

    Provenance fallback for save paths that don't hold the live client (the
    one-shot scan/raw/discover producers): a command resolves its transport once,
    so the config-resolved type matches what was used. The streaming producers
    (monitor/multi) pass the client's own ``diag.transport`` instead, which is
    authoritative. Returns None if resolution fails.
    """
    try:
        from .transport import resolve_transport

        return resolve_transport(None).type
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Metadata prompt
# ---------------------------------------------------------------------------


def prompt_metadata(
    suggested_label: str = "",
    last_state: list | str | None = None,
) -> tuple[str, list, str] | None:
    """Prompt user for session label, vehicle_states, notes.

    Returns ``(label, vehicle_states, notes)`` (vehicle_states is a token list)
    or None if cancelled. The suggested_label is shown in brackets and accepted
    on Enter. last_state (a list or string) is shown as the default and re-used
    across saves when the user just presses Enter.
    """
    last_str = _join_states(last_state)
    try:
        if suggested_label:
            label = input(f"  Session label [{suggested_label}]: ").strip()
            if not label:
                label = suggested_label
        else:
            label = input("  Session label (required, empty to skip): ").strip()
            if not label:
                print("  Cancelled (empty label).")
                return None

        state_prompt = (
            f"  States [{last_str}]: "
            if last_str
            else "  States (comma-separated, e.g. sleep, acc, charging) []: "
        )
        raw = input(state_prompt).strip()
        if not raw and last_str:
            raw = last_str

        notes = input("  Notes []: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return None

    return label, _parse_states(raw), notes


def resolve_metadata(
    label: str | None,
    vehicle_states: list | str | None,
    notes: str | None,
    suggested_label: str = "",
    last_state: list | str | None = None,
) -> tuple[str, list, str] | None:
    """Resolve session metadata, non-interactively when a label is supplied.

    If ``label`` is given (e.g. from the ``--label`` CLI flag), use the flag
    values directly and do NOT prompt — this is what agents/scripts use. When
    ``label`` is None, fall back to the interactive :func:`prompt_metadata`.
    Returns ``(label, vehicle_states, notes)`` (vehicle_states as a token list)
    or None if cancelled.
    """
    if label is not None:
        return label, _parse_states(vehicle_states), (notes or "")
    return prompt_metadata(suggested_label=suggested_label, last_state=last_state)


# ---------------------------------------------------------------------------
# Session builders
# ---------------------------------------------------------------------------


def build_query_session(
    results: list[tuple[str, str, str, str]] | list[tuple[str, str, str, str, int | None]],
    label: str,
    vehicle_states: list,
    notes: str,
    keep_mode: str | None = None,
    date: str | None = None,
    transport: str | None = None,
    quality: dict | None = None,
) -> CaptureSession:
    """Build a capture session dict from query/raw payload results.

    ``results`` is a list of ``(ecu_ref, pid, hex, time)`` tuples (``time``
    may be an empty string), optionally with a 5th ``elapsed_ms`` element
    (``None`` when unavailable — e.g. monitor/batched rows). ``ecu_ref`` is the
    ECU CAN response address as a hex string (e.g. ``"0x7EC"``), stored in each
    capture's ``rx`` field. Captures are grouped by ECU then PID in the order
    given. Decoded parameter values are intentionally NOT stored — they are
    regenerated on demand from the payload + PID definitions.

    ``date`` sets the session date (the acquisition date, from the journal's
    per-record dates); it falls back to today when omitted, for the direct
    (non-journaled) callers. Persisting the true capture date keeps a
    midnight-crossing session's samples aligned to the right calendar day.

    ``keep_mode`` records how the monitor deduplicated payloads. ``"changes"``
    (run-length: only value-transitions kept) and ``"unique"`` (legacy global
    dedup: only globally-distinct values kept) are persisted on the session so
    later analysis knows how to read its timing/transitions; ``"all"``/``"last"``
    keep every polled sample and are not flagged.

    ``transport`` records how the payloads were acquired (the transport label,
    e.g. ``"slcan-tcp"``) and ``quality`` a small exchange/error footprint —
    provenance for judging how trustworthy the session's data is. Both are
    omitted when not supplied.
    """
    session: dict = {
        "date": date or datetime.now().strftime("%Y-%m-%d"),
        "label": label,
    }
    if vehicle_states:
        session["vehicle_states"] = list(vehicle_states)
    if notes:
        session["notes"] = notes
    if keep_mode in ("changes", "unique"):
        session["keep_mode"] = keep_mode
    if transport:
        session["transport"] = transport
    if quality:
        session["quality"] = dict(quality)

    captures: list[CaptureRecord] = []
    for row in results:
        ecu_ref, pid, hex_val, ts = row[0], row[1], row[2], row[3]
        elapsed_ms: int | None = row[4] if len(row) > 4 else None  # ty: ignore[index-out-of-bounds]
        capture: CaptureRecord = {
            "rx": ecu_ref,
            "pid": pid,
            "payload": hex_val.upper(),
        }
        # A payload capture is a time-series sample: it must always carry a
        # timestamp so cross-signal time-alignment can use it (Tranche 2.6). Fall
        # back to the current time when the caller didn't supply one.
        capture["time"] = ts or datetime.now().strftime("%H:%M:%S")
        if elapsed_ms is not None:
            capture["elapsed_ms"] = elapsed_ms
        captures.append(capture)

    session["captures"] = captures
    return cast(CaptureSession, session)


def build_scan_session(
    ecu_ref: str,
    tx_id: int,
    service: int,
    pid_range: tuple[int, int],
    positive: list[tuple[int, UdsResponse]],
    negative: list[tuple[int, int, str]],
    errors: list[tuple[int, str]],
    label: str,
    vehicle_states: list,
    notes: str,
    append_bytes: str = "",
    session_flag: bool = False,
) -> CaptureSession:
    """Build a capture session dict from scan results."""
    start, end = pid_range
    wide_did = service in (0x22, 0x2F, 0x31)
    did_fmt = "04X" if wide_did else "02X"

    range_str = f"{start:{did_fmt}}-{end:{did_fmt}}"
    suffix = f" + suffix {append_bytes}" if append_bytes else ""

    session: dict = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "label": label,
    }
    if vehicle_states:
        session["vehicle_states"] = list(vehicle_states)
    if notes:
        session["notes"] = notes

    # Build scan_results capture
    scan_capture: CaptureRecord = {
        "rx": ecu_ref,
        "pid": f"scan {service:02X} {range_str}{suffix}",
    }

    scan_results: ScanResults = {}
    if positive:
        responding: list[RespondingEntry] = []
        for pid_val, resp in positive:
            entry: RespondingEntry = {
                "did": f"{pid_val:{did_fmt}}",
                "response": f"{len(resp['bytes'])} bytes",
            }
            hex_str = resp["hex"]
            if len(hex_str) > 80:
                hex_str = hex_str[:80] + "..."
            entry["notes"] = f"Raw: {hex_str}"
            responding.append(entry)
        scan_results["responding"] = responding

    n_rejected = len(negative) + len(errors)
    if n_rejected:
        parts = []
        if negative:
            parts.append(f"{len(negative)} NRC")
        if errors:
            parts.append(f"{len(errors)} errors")
        scan_results["rejected"] = f"{n_rejected} DIDs returned {' + '.join(parts)}"

    scan_capture["scan_results"] = scan_results
    session["captures"] = [scan_capture]
    return cast(CaptureSession, session)


def build_raw_session(
    ecu_ref: str,
    tx_id: int,
    request: str,
    response: UdsResponse,
    label: str,
    vehicle_states: list,
    notes: str,
    pids_data: dict | None = None,
) -> CaptureSession:
    """Build a capture session dict from a raw UDS response.

    Decoded parameter values are intentionally NOT stored — they are derived
    data, regenerated on demand from the payload + PID definitions (see
    decode.py and query-captures.py).
    """
    session: dict = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "label": label,
    }
    if vehicle_states:
        session["vehicle_states"] = list(vehicle_states)
    if notes:
        session["notes"] = notes

    capture: CaptureRecord = {
        "rx": ecu_ref,
        "pid": request,
    }

    if response["ok"]:
        capture["payload"] = response["hex"].upper()
    else:
        if response.get("nrc") is not None:
            capture["response"] = f"NRC 0x{response['nrc']:02X} ({response['nrc_desc']})"
        else:
            capture["response"] = response.get("error", "unknown error")

    session["captures"] = [capture]
    return cast(CaptureSession, session)


def build_discover_session(
    alive: list[tuple[int, str, str]],
    silent_count: int,
    error_count: int,
    addr_range: tuple[int, int],
    label: str,
    vehicle_states: list,
    notes: str,
) -> CaptureSession:
    """Build a capture session dict from discovery scan results.

    The top-level ``rx`` is the ``broadcast`` sentinel (a discovery scan spans
    many ECUs). Each responder's originating ECU is preserved as its CAN
    response address (RX = TX + 8) in ``scan_results.responding[].rx``.
    """
    from .ecus import rx_addr_str

    start, end = addr_range
    session: dict = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "label": label,
    }
    if vehicle_states:
        session["vehicle_states"] = list(vehicle_states)
    if notes:
        session["notes"] = notes

    capture: CaptureRecord = {
        "rx": "broadcast",
        "pid": f"discover {start:03X}-{end:03X}",
    }

    scan_results: ScanResults = {}
    if alive:
        responding: list[RespondingEntry] = []
        for tx_id, ecu_label, resp_hex in alive:
            entry: RespondingEntry = {
                "rx": rx_addr_str(tx_id),
                "response": ecu_label,
                "notes": f"Raw: {resp_hex}" if resp_hex else "",
            }
            responding.append(entry)
        scan_results["responding"] = responding

    total_silent = silent_count + error_count
    if total_silent:
        scan_results["rejected"] = f"{total_silent} addresses silent"

    capture["scan_results"] = scan_results
    session["captures"] = [capture]
    return cast(CaptureSession, session)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def build_manual_session(
    captures: list[CaptureRecord],
    *,
    label: str,
    date: str | None = None,
    vehicle_states: list[str] | None = None,
    notes: str | None = None,
    transport: str | None = "import",
) -> CaptureSession:
    """Assemble a session dict from manually-supplied captures (the import path).

    Mirrors the shape produced by the ``--save`` streaming path so an imported
    session is indistinguishable from a device-recorded one. ``date`` defaults to
    today; ``vehicle_states``/``notes`` are omitted when empty. ``transport``
    defaults to ``"import"`` (these payloads didn't come off a live bus here), so
    imported data is distinguishable from device-recorded data in provenance.
    Field order follows the schema (date, label, [vehicle_states], [notes],
    [transport], captures).
    """
    session: dict = {
        "date": date or datetime.now().strftime("%Y-%m-%d"),
        "label": label,
    }
    if vehicle_states:
        session["vehicle_states"] = list(vehicle_states)
    if notes:
        session["notes"] = notes
    if transport:
        session["transport"] = transport
    session["captures"] = list(captures)
    return cast(CaptureSession, session)


def _stamp_version(session: CaptureSession) -> CaptureSession:
    """Return a copy of ``session`` with the recording canair ``version`` stamped.

    Called from :func:`save_session` — the single choke point every session flows
    through before landing on disk — so every recorded session carries the tool
    version that wrote it (provenance for debugging capture issues traced to a
    specific release). An existing ``version`` (e.g. on a recovered/re-saved
    session) is left untouched. Inserted right after ``label`` so the on-disk
    field order stays readable (date, label, version, …).
    """
    if session.get("version"):
        return session
    from . import __version__

    rebuilt: dict = {}
    for key, value in session.items():
        rebuilt[key] = value
        if key == "label":
            rebuilt["version"] = __version__
    if "version" not in rebuilt:
        # No ``label`` key (shouldn't happen for a valid session) — append it.
        rebuilt["version"] = __version__
    return cast(CaptureSession, rebuilt)


def save_session(session: CaptureSession, captures_dir: Path | None = None) -> Path:
    """Append a session dict to captures/YYYY-MM-DD.json. Returns the file path.

    The file is named after the session's own ``date`` (falling back to today),
    so a session captured on — or recovered from — a different day lands in the
    correct per-day file rather than today's. When ``captures_dir`` is None, the
    active vehicle profile's captures/ directory is used.

    Every session is stamped with the recording canair ``version`` here (the one
    place all save paths funnel through), unless it already carries one.
    """
    session = _stamp_version(session)
    if captures_dir is None:
        from .profile import active

        captures_dir = active().captures_dir
    # Use the session's own date (leading YYYY-MM-DD, tolerating a same-day
    # suffix like "2026-04-17-b") so the file matches when the payloads were
    # actually acquired; only fall back to "now" when the session has no date.
    raw_date = str(session.get("date") or "").strip()
    file_date = raw_date[:10] if len(raw_date) >= 10 else datetime.now().strftime("%Y-%m-%d")
    capture_file = captures_dir / f"{file_date}{capture_io.CAPTURE_SUFFIX}"

    if capture_file.exists():
        data = capture_io.load_capture_file(capture_file)
        if not data or "sessions" not in data:
            data = {"sessions": []}
        data["sessions"].append(session)
    else:
        data = {"sessions": [session]}

    capture_io.dump_capture_file(capture_file, data)

    n_captures = len(session.get("captures", []))
    print(f"  \u2192 Saved {n_captures} capture(s) to {capture_file.name}")
    return capture_file


def save_session_journaled(
    session: CaptureSession, captures_dir: Path | None = None
) -> Path | None:
    """Save a pre-built session via a write-ahead journal (crash-safe path).

    Used by the one-shot producers (scan/raw/discover) so every save path shares
    the same recover-on-crash behaviour as streaming query/monitor. The session
    is written to a journal, then reconciled into ``captures/YYYY-MM-DD.yaml`` and
    the journal removed. Returns the capture file path (or None if empty).

    The session's own ``transport`` (when set by the caller from the active
    client) is carried through the journal meta so recovered one-shot captures
    keep their provenance.
    """
    from .capture_journal import CaptureJournal

    if captures_dir is None:
        from .profile import active

        captures_dir = active().captures_dir

    journal = CaptureJournal.open(
        captures_dir,
        label=session.get("label", ""),
        vehicle_states=session.get("vehicle_states"),
        notes=session.get("notes"),
        source="oneshot",
        transport=session.get("transport") or active_transport_label(),
    )
    journal.append_session(session)
    return journal.reconcile()


def _write_captures_file(fpath: Path, data: CaptureFile) -> None:
    """Serialize a capture-file dict back to disk (JSON)."""
    capture_io.dump_capture_file(fpath, data)


def set_capture_note(fpath: Path, session_idx: int, capture_idx: int, note: str) -> None:
    """Set (or clear) the ``notes`` field on one capture, addressed by index.

    A non-empty ``note`` is stored verbatim; an empty/blank note removes the
    field entirely. Raises IndexError if the indices don't resolve.
    """
    data = capture_io.load_capture_file(fpath)
    cap = data["sessions"][session_idx]["captures"][capture_idx]
    note = note.strip()
    if note:
        cap["notes"] = note
    else:
        cap.pop("notes", None)
    _write_captures_file(fpath, data)


def set_session_note(fpath: Path, session_idx: int, note: str) -> None:
    """Set (or clear) the ``notes`` field on one session, addressed by index.

    A non-empty ``note`` is stored verbatim; an empty/blank note removes the
    field entirely. Raises IndexError if the index doesn't resolve. Use this
    instead of hand-editing a session's ``notes`` in a capture file.
    """
    data = capture_io.load_capture_file(fpath)
    session = data["sessions"][session_idx]
    note = note.strip()
    if note:
        session["notes"] = note
    else:
        session.pop("notes", None)
    _write_captures_file(fpath, data)


def set_session_keep_mode(fpath: Path, session_idx: int, keep_mode: str | None) -> None:
    """Set (or clear) the ``keep_mode`` field on one session, addressed by index.

    Only ``"changes"`` (run-length) and ``"unique"`` (legacy global dedup) are
    meaningful — they tell later analysis how the payloads were deduplicated; any
    other value clears the field. Use this to backfill sessions captured before
    ``keep_mode`` was persisted, instead of hand-editing a capture file.
    """
    data = capture_io.load_capture_file(fpath)
    session = data["sessions"][session_idx]
    if keep_mode in ("changes", "unique"):
        session["keep_mode"] = keep_mode
    else:
        session.pop("keep_mode", None)
    _write_captures_file(fpath, data)


def set_session_states(fpath: Path, session_idx: int, vehicle_states) -> None:
    """Set (or clear) the ``vehicle_states`` field on one session, by index.

    ``vehicle_states`` is normalized to a token list (a comma-separated string or
    a list are both accepted); a non-empty list is stored, an empty one removes
    the field. Raises IndexError if the index doesn't resolve. Use this instead
    of hand-editing a session's ``vehicle_states`` in a capture file — e.g. to
    back-fill a session that reconciled with an empty state before the monitor's
    span-aware state back-fill existed.
    """
    data = capture_io.load_capture_file(fpath)
    session = data["sessions"][session_idx]
    states = _parse_states(vehicle_states)
    if states:
        session["vehicle_states"] = states
    else:
        session.pop("vehicle_states", None)
    _write_captures_file(fpath, data)


def delete_capture(fpath: Path, session_idx: int, capture_idx: int) -> bool:
    """Delete one capture, addressed by index. Returns True if its (now empty)
    session was removed too. Raises IndexError if the indices don't resolve.
    """
    data = capture_io.load_capture_file(fpath)
    captures = data["sessions"][session_idx]["captures"]
    del captures[capture_idx]
    removed_session = not captures
    if removed_session:
        del data["sessions"][session_idx]
    _write_captures_file(fpath, data)
    return removed_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_payload(
    ecu_name: str,
    pid: str,
    hex_payload: str,
    pids_data: dict,
    ecu_index: dict | None = None,
) -> dict | None:
    """Try to decode a payload using PID definitions. Returns decoded dict or None.

    Decoded values are never persisted to capture files; this helper is used to
    regenerate them on demand for display (see query-captures.py). Pass a
    prebuilt ``ecu_index`` to avoid rebuilding it on every call.
    """
    from .autopid_layout import uds_hex_to_wican_bytes
    from .decode_value import NUMERIC, decode_typed, render
    from .expression import evaluate_expression
    from .pids import build_ecu_index

    if ecu_index is None:
        ecu_index = build_ecu_index(pids_data)
    ecu_key = ecu_name.upper()
    if ecu_key not in ecu_index:
        return None

    ecu_def = ecu_index[ecu_key]
    pid_info = ecu_def.get("pids", {}).get(pid)
    if not pid_info or not pid_info.get("parameters"):
        return None

    try:
        wican_bytes = uds_hex_to_wican_bytes(hex_payload)
        decoded = {}
        for pname, pdef in pid_info["parameters"].items():
            expr = pdef.get("expression", "")
            ptype = (pdef.get("type") or NUMERIC).lower()
            unit = pdef.get("unit", "")
            # Typed (enum/bitmask/ascii/date/bcd/struct) params: render the typed
            # interpretation (e.g. an enum label) rather than the bare float.
            if ptype != NUMERIC:
                try:
                    decoded[pname] = render(decode_typed(pdef, wican_bytes), unit)
                except Exception:
                    pass
                continue
            if not expr:
                continue
            try:
                value = evaluate_expression(expr, wican_bytes)
                value = round(value * 100) / 100
                display = pdef.get("display", "")
                if display:
                    try:
                        v = value  # noqa: F841 — used by eval(display)
                        formatted = eval(display)
                        decoded[pname] = f"{value} {unit} ({formatted})".strip()
                    except Exception:
                        decoded[pname] = f"{value} {unit}".strip()
                else:
                    decoded[pname] = f"{value} {unit}".strip()
            except Exception:
                pass
        return decoded if decoded else None
    except Exception:
        return None


def suggest_scan_label(
    ecu_name: str,
    service: int,
    pid_range: tuple[int, int],
    append_bytes: str = "",
) -> str:
    """Generate a suggested label for a scan session."""
    start, end = pid_range
    wide_did = service in (0x22, 0x2F, 0x31)
    did_fmt = "04X" if wide_did else "02X"
    suffix = f" +{append_bytes}" if append_bytes else ""
    return f"Scan {ecu_name} {service:02X} {start:{did_fmt}}-{end:{did_fmt}}{suffix}"


def suggest_raw_label(ecu_name: str, request: str) -> str:
    """Generate a suggested label for a raw request capture."""
    return f"Raw {ecu_name} {request}"


def suggest_discover_label(addr_range: tuple[int, int]) -> str:
    """Generate a suggested label for a discovery scan."""
    start, end = addr_range
    return f"Discovery scan {start:03X}-{end:03X}"
