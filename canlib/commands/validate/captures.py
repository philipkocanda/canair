"""captures/ validation — payload files vs captures_schema.json + soft warnings."""

import json
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from canlib import capture_io

from ._common import CAPTURES_SCHEMA_FILE, DEPRECATED_FIELDS


def load_valid_rx_addrs() -> set[int]:
    """Set of valid ECU CAN response addresses (RX = request TX + 8)."""
    from canlib.ecus import build_rx_index

    return set(build_rx_index())


def _path_str(abs_path) -> str:
    """Render a jsonschema error path (deque) as e.g. sessions[0].captures[3].ecu."""
    out = ""
    for part in abs_path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else part
    return out or "<root>"


def validate_captures_file(path: Path, validator: Validator, rx_addrs: set[int]) -> list[str]:
    from canlib.ecus import SENTINELS, parse_ecu_ref

    errors: list[str] = []
    data = capture_io.load_capture_file(path)

    # Schema validation (structure, types, required/allowed fields, patterns).
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        loc = _path_str(err.absolute_path)
        # Nicer message for known-deprecated fields flagged by additionalProperties.
        dep = DEPRECATED_FIELDS & set(err.instance) if isinstance(err.instance, dict) else set()
        if err.validator == "additionalProperties" and dep:
            errors.append(f"{loc}: deprecated field(s): {sorted(dep)}")
        else:
            errors.append(f"{loc}: {err.message}")

    # Cross-file / cross-field checks not expressible in JSON Schema.
    if isinstance(data, dict):
        for si, session in enumerate(data.get("sessions", []) or []):
            if not isinstance(session, dict):
                continue
            date = session.get("date", "")
            if date:
                # Schema checks the shape; verify the leading date is a real
                # calendar date (a "-<suffix>" for same-day sessions is allowed).
                try:
                    datetime.strptime(str(date)[:10], "%Y-%m-%d")
                except ValueError:
                    errors.append(f"sessions[{si}]: date '{date}' is not a valid calendar date")
                if path.stem != date:
                    errors.append(
                        f"sessions[{si}]: date '{date}' doesn't match filename '{path.stem}'"
                    )

            for ci, cap in enumerate(session.get("captures", []) or []):
                if not isinstance(cap, dict):
                    continue
                ecu = capture_io.capture_rx(cap) or None
                if ecu and str(ecu).lower() not in SENTINELS:
                    rx = parse_ecu_ref(ecu)
                    if rx is not None and rx not in rx_addrs:
                        errors.append(
                            f"sessions[{si}].captures[{ci}].rx: response address "
                            f"'{ecu}' is not a known ECU response address (RX = TX + 8)"
                        )

    return errors


def _run_captures(strict: bool = False) -> int:
    with open(CAPTURES_SCHEMA_FILE) as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    rx_addrs = load_valid_rx_addrs()

    from canlib.profile import active

    captures_dir = active().captures_dir
    capture_io.ensure_migrated(captures_dir)
    files = capture_io.iter_capture_files(captures_dir)

    if not files:
        print("No capture files found.")
        return 0

    # State vocabulary for soft warnings (empty when no vehicle_states.yaml → no warnings).
    from canlib.states import state_names

    vocab = {n.lower() for n in state_names()}

    total_errors = 0
    total_warnings = 0
    total_time_gaps = 0
    for path in files:
        errors = validate_captures_file(path, validator, rx_addrs)
        warnings = _capture_state_warnings(path, vocab) if vocab else []
        warnings += _capture_echo_warnings(path)
        warnings += _capture_nonhex_warnings(path)
        warnings += _capture_quality_warnings(path)
        # Missing-time on payload captures: an error under --strict (new-data
        # gate), otherwise a soft warning (existing rows grandfathered).
        time_gaps = _capture_missing_time_warnings(path)
        total_time_gaps += len(time_gaps)
        if strict:
            errors = list(errors) + time_gaps
        else:
            warnings += time_gaps
        if errors:
            print(f"\n{path.name}: {len(errors)} errors")
            for e in errors:
                print(f"  - {e}")
            total_errors += len(errors)
        else:
            print(f"{path.name}: OK")
        for w in warnings:
            print(f"  ⚠ {w}")
        total_warnings += len(warnings)

    if total_warnings:
        print(
            f"\n{total_warnings} warning(s) — see `canair validate states` / echo mismatches above"
        )
    if not strict and total_time_gaps:
        print(
            f"  ({total_time_gaps} untimed payload capture(s); run "
            "`canair validate captures --strict` to treat as errors)"
        )
    if total_errors:
        print(f"\n{total_errors} total errors across {len(files)} files")
        return 1
    else:
        print(f"\nAll {len(files)} files valid.")
        return 0


def _capture_state_warnings(path: Path, vocab: set[str]) -> list[str]:
    """Soft warnings for session vehicle_states outside the declared vocabulary.

    A session's ``vehicle_states`` is a list of tokens (e.g. [ready, parked]); a
    session is flagged only when *none* of its tokens is a known state name
    (case-insensitive). Never an error — this only nudges toward the
    standardized vehicle_states.yaml vocabulary.
    """
    warnings: list[str] = []
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        states = session.get("vehicle_states")
        if not states:
            continue
        if not isinstance(states, list):
            warnings.append(f"sessions[{si}]: vehicle_states must be a list")
            continue
        tokens = [str(t).strip().lower() for t in states if str(t).strip()]
        if tokens and not any(t in vocab for t in tokens):
            warnings.append(
                f"sessions[{si}]: vehicle_states {states} has no token in the "
                f"vehicle_states.yaml vocabulary"
            )
    return warnings


def _capture_quality_warnings(path: Path) -> list[str]:
    """Soft warnings for sessions recorded with dropped/stale ISO-TP frames.

    A session's ``quality`` footprint (written at capture time from the transport
    diagnostics recorder) records ``drop``/``stale`` counts — dropped, duplicated,
    out-of-order, or truncated multi-frame reads, and stale frames leaking between
    requests. Those are exactly the faults that silently corrupted multi-frame
    payloads before the ISO-TP reassembly hardening, so a non-zero count is a
    trustworthiness flag on that session's multi-frame captures. Reported as a
    warning, never an error — the payloads that survived the guards are fine; this
    only nudges you to re-capture if a multi-frame PID looks off. ``no_data``/
    ``bus``/``decode`` are non-answers (nothing was stored), so they don't warn.
    """
    warnings: list[str] = []
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        quality = session.get("quality")
        if not isinstance(quality, dict):
            continue
        drops = (quality.get("drop", 0) or 0) + (quality.get("stale", 0) or 0)
        if drops:
            transport = session.get("transport", "?")
            warnings.append(
                f"sessions[{si}]: recorded {drops} dropped/stale ISO-TP frame(s) "
                f"during capture (transport={transport}) — multi-frame payloads in "
                "this session may be unreliable"
            )
    return warnings


def _capture_echo_warnings(path: Path) -> list[str]:
    """Soft warnings for captures whose payload doesn't echo their recorded PID.

    A UDS positive response echoes the request SID (+0x40) and identifier bytes;
    a ``6101…`` payload stored under a ``2102`` request is a stale/misfiled frame
    (the ELM327 leaks a previous request's late response into the next read — see
    ``uds_parse.payload_echo_mismatch``). Reported as a warning, never an error,
    since free-form/raw captures and multi-frame quirks shouldn't hard-fail.
    """
    from canlib.uds_parse import payload_echo_mismatch

    warnings: list[str] = []
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        for ci, cap in enumerate(session.get("captures", []) or []):
            if not isinstance(cap, dict):
                continue
            pid = cap.get("pid")
            payload = cap.get("payload")
            if not pid or not payload:
                continue
            reason = payload_echo_mismatch(str(pid), str(payload))
            if reason:
                warnings.append(
                    f"sessions[{si}].captures[{ci}] ({capture_io.capture_rx(cap) or '?'} {pid} "
                    f"@ {cap.get('time', '?')}): {reason}"
                )
    return warnings


def _capture_nonhex_warnings(path: Path) -> list[str]:
    """Soft warnings for captures whose payload isn't a valid UDS byte string.

    Payloads are recorded by the tool as raw response hex, so a non-hex one
    (an ELM327 status string like ``NO DATA``, free-text notes, or a mixed
    hex+ASCII transcription) signals a mis-recorded capture — see
    ``uds_parse.payload_not_hex``. Reported as a warning, never an error.
    """
    from canlib.uds_parse import payload_not_hex

    warnings: list[str] = []
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        for ci, cap in enumerate(session.get("captures", []) or []):
            if not isinstance(cap, dict):
                continue
            payload = cap.get("payload")
            if not payload:
                continue
            reason = payload_not_hex(str(payload))
            if reason:
                warnings.append(
                    f"sessions[{si}].captures[{ci}] ({capture_io.capture_rx(cap) or '?'} "
                    f"{cap.get('pid', '?')} @ {cap.get('time', '?')}): {reason}"
                )
    return warnings


def _capture_missing_time_warnings(path: Path) -> list[str]:
    """Soft warnings for time-series (``payload``) captures with no usable ``time``.

    A payload capture is a time-series sample and should carry a timestamp so
    cross-signal time-alignment (``canair correlate``/``hunt``) can use it. One-shot
    ``scan_results``/``response`` captures are exempt — a timestamp was never
    meaningful there. Existing untimed payload rows are grandfathered (warning,
    not error, unless ``validate captures --strict``). See Tranche 2.6.
    """
    from canlib.capture_dates import entry_datetime

    warnings: list[str] = []
    data = capture_io.load_capture_file(path)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        date = session.get("date", "")
        for ci, cap in enumerate(session.get("captures", []) or []):
            if not isinstance(cap, dict) or not cap.get("payload"):
                continue  # only payload (time-series) captures; scans exempt
            # entry_datetime needs the session date + capture time.
            if entry_datetime({"date": date, "time": cap.get("time", "")}) is None:
                warnings.append(
                    f"sessions[{si}].captures[{ci}] ({capture_io.capture_rx(cap) or '?'} "
                    f"{cap.get('pid', '?')}): payload capture has no usable time "
                    "(excluded from time-aligned analysis)"
                )
    return warnings
