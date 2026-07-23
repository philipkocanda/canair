"""Shared capture file save logic.

Provides functions for prompting session metadata and appending captures
to per-date YAML files in captures/. Used by scan, raw, discover, and
monitor modes.
"""

from datetime import datetime
from pathlib import Path

from .states import join_states as _join_states
from .states import parse_states as _parse_states
from .yaml_rt import dump as _dump
from .yaml_rt import round_trip_yaml as _yaml

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
            f"  States [{last_str}]: " if last_str
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
    results: list[tuple[str, str, str, str]],
    label: str,
    vehicle_states: list,
    notes: str,
) -> dict:
    """Build a capture session dict from query/raw payload results.

    ``results`` is a list of ``(ecu_ref, pid, hex, time)`` tuples (``time``
    may be an empty string). ``ecu_ref`` is the ECU CAN response address as a
    hex string (e.g. ``"0x7EC"``). Captures are grouped by ECU then PID in the
    order given. Decoded parameter values are intentionally NOT stored — they
    are regenerated on demand from the payload + PID definitions.
    """
    session: dict = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "label": label,
    }
    if vehicle_states:
        session["vehicle_states"] = list(vehicle_states)
    if notes:
        session["notes"] = notes

    captures: list[dict] = []
    for ecu_ref, pid, hex_val, ts in results:
        capture: dict = {
            "ecu": ecu_ref,
            "pid": pid,
            "payload": hex_val.upper(),
        }
        if ts:
            capture["time"] = ts
        captures.append(capture)

    session["captures"] = captures
    return session

def build_scan_session(
    ecu_ref: str,
    tx_id: int,
    service: int,
    pid_range: tuple[int, int],
    positive: list[tuple[int, dict]],
    negative: list[tuple[int, int, str]],
    errors: list[tuple[int, str]],
    label: str,
    vehicle_states: list,
    notes: str,
    append_bytes: str = "",
    session_flag: bool = False,
) -> dict:
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
    scan_capture: dict = {
        "ecu": ecu_ref,
        "pid": f"scan {service:02X} {range_str}{suffix}",
    }

    scan_results: dict = {}
    if positive:
        responding = []
        for pid_val, resp in positive:
            entry: dict = {
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
    return session


def build_raw_session(
    ecu_ref: str,
    tx_id: int,
    request: str,
    response: dict,
    label: str,
    vehicle_states: list,
    notes: str,
    pids_data: dict | None = None,
) -> dict:
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

    capture: dict = {
        "ecu": ecu_ref,
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
    return session


def build_discover_session(
    alive: list[tuple[int, str, str]],
    silent_count: int,
    error_count: int,
    addr_range: tuple[int, int],
    label: str,
    vehicle_states: list,
    notes: str,
) -> dict:
    """Build a capture session dict from discovery scan results.

    The top-level ``ecu`` is the ``broadcast`` sentinel (a discovery scan spans
    many ECUs). Each responder's originating ECU is preserved as its CAN
    response address (RX = TX + 8) in ``scan_results.responding[].ecu``.
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

    capture: dict = {
        "ecu": "broadcast",
        "pid": f"discover {start:03X}-{end:03X}",
    }

    scan_results: dict = {}
    if alive:
        responding = []
        for tx_id, ecu_label, resp_hex in alive:
            entry: dict = {
                "ecu": rx_addr_str(tx_id),
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
    return session


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def save_session(session: dict, captures_dir: Path | None = None) -> Path:
    """Append a session dict to captures/YYYY-MM-DD.yaml. Returns the file path.

    Existing content (including comments) is preserved via a ruamel round-trip;
    only the newly appended session is rendered fresh. When ``captures_dir`` is
    None, the active vehicle profile's captures/ directory is used.
    """
    if captures_dir is None:
        from .profile import active

        captures_dir = active().captures_dir
    today = datetime.now().strftime("%Y-%m-%d")
    capture_file = captures_dir / f"{today}.yaml"

    y = _yaml()
    if capture_file.exists():
        with open(capture_file) as f:
            data = y.load(f)
        if not data or "sessions" not in data:
            data = {"sessions": []}
        data["sessions"].append(session)
    else:
        data = {"sessions": [session]}

    with open(capture_file, "w") as f:
        _dump(data, f)

    n_captures = len(session.get("captures", []))
    print(f"  \u2192 Saved {n_captures} capture(s) to {capture_file.name}")
    return capture_file


def save_session_journaled(session: dict, captures_dir: Path | None = None) -> Path | None:
    """Save a pre-built session via a write-ahead journal (crash-safe path).

    Used by the one-shot producers (scan/raw/discover) so every save path shares
    the same recover-on-crash behaviour as streaming query/monitor. The session
    is written to a journal, then reconciled into ``captures/YYYY-MM-DD.yaml`` and
    the journal removed. Returns the capture file path (or None if empty).
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
    )
    journal.append_session(session)
    return journal.reconcile()


def _write_captures_file(fpath: Path, data: dict) -> None:
    """Serialize a capture-file dict back to disk (comment-preserving)."""
    with open(fpath, "w") as f:
        _dump(data, f)


def set_capture_note(fpath: Path, session_idx: int, capture_idx: int, note: str) -> None:
    """Set (or clear) the ``notes`` field on one capture, addressed by index.

    A non-empty ``note`` is stored verbatim; an empty/blank note removes the
    field entirely. Raises IndexError if the indices don't resolve.
    """
    with open(fpath) as f:
        data = _yaml().load(f)
    cap = data["sessions"][session_idx]["captures"][capture_idx]
    note = note.strip()
    if note:
        cap["notes"] = note
    else:
        cap.pop("notes", None)
    _write_captures_file(fpath, data)


def delete_capture(fpath: Path, session_idx: int, capture_idx: int) -> bool:
    """Delete one capture, addressed by index. Returns True if its (now empty)
    session was removed too. Raises IndexError if the indices don't resolve.
    """
    with open(fpath) as f:
        data = _yaml().load(f)
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
    from .expression import evaluate_expression
    from .pids import build_ecu_index
    from .wican_bytes import uds_hex_to_wican_bytes

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
            if not expr:
                continue
            try:
                value = evaluate_expression(expr, wican_bytes)
                value = round(value * 100) / 100
                unit = pdef.get("unit", "")
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
