"""Building a capture session from the results of one live operation."""

from collections.abc import Sequence
from datetime import datetime

from canair.captures.keepmode import KeepMode, persisted_keep_mode
from canair.captures.link_events import LinkEvent
from canair.captures.types import (
    CaptureRecord,
    CaptureSession,
    Quality,
    RespondingEntry,
    ScanResults,
    SessionMeta,
)
from canair.core.id_ranges import IdRange, as_id_ranges, format_id_ranges
from canair.uds.response import UdsResponse
from canair.uds.services import identifier_width

# The ``transport`` of a device-free session (``canair import uds``): its payloads
# never came off a live bus here.
IMPORT_TRANSPORT = "import"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _session_meta(
    vehicle_states: Sequence[str] | None,
    notes: str | None,
    transport: str | None = None,
) -> SessionMeta:
    """The metadata every builder shares, omitting what wasn't supplied.

    Splat into the session literal (``{"date": …, "label": …, **meta, "captures":
    …}``) so the on-disk field order stays date → metadata → captures. See
    :class:`~canair.captures.types.SessionMeta` for why the metadata is accumulated
    separately rather than written into the session after construction.
    ``transport`` is the live client's label (:func:`~canair.transport.stats.
    client_transport`) — omitted when unknown rather than guessed.
    """
    meta: SessionMeta = {}
    if vehicle_states:
        meta["vehicle_states"] = list(vehicle_states)
    if notes:
        meta["notes"] = notes
    if transport:
        meta["transport"] = transport
    return meta


def build_query_session(
    results: list[tuple[str, str, str, str]] | list[tuple[str, str, str, str, int | None]],
    label: str,
    vehicle_states: list,
    notes: str,
    keep_mode: KeepMode | None = None,
    date: str | None = None,
    transport: str | None = None,
    quality: Quality | None = None,
    non_answers: Sequence[tuple[str, str, str]] = (),
    link_events: Sequence[LinkEvent] = (),
) -> CaptureSession:
    """Build a capture session dict from query/raw payload results.

    ``results`` is a list of ``(ecu_ref, pid, hex, time)`` tuples (``time``
    may be an empty string), optionally with a 5th ``elapsed_ms`` element
    (``None`` when unavailable — e.g. monitor/batched rows). ``ecu_ref`` is the
    ECU CAN response address as a hex string (e.g. ``"0x7EC"``), stored in each
    capture's ``rx`` field. Captures are grouped by ECU then PID in the order
    given. Decoded parameter values are intentionally NOT stored — they are
    regenerated on demand from the payload + PID definitions.

    ``non_answers`` holds ``(ecu_ref, pid, response)`` for requests that drew an
    NRC or no reply (``response`` as :func:`non_answer_text` renders it). They
    follow the payload captures, untimed like :func:`build_raw_session`'s,
    because a refusal is a fact about the request, not a time-series sample.

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
    omitted when not supplied. ``link_events`` (when the recording could not see
    the car, :mod:`canair.captures.link_events`) likewise.
    """
    meta = _session_meta(vehicle_states, notes)
    persisted = persisted_keep_mode(keep_mode)
    if persisted is not None:
        meta["keep_mode"] = persisted
    if transport:
        meta["transport"] = transport
    if quality:
        # Copied so the session doesn't alias the caller's footprint.
        meta["quality"] = quality.copy()
    if link_events:
        meta["link_events"] = [event.copy() for event in link_events]

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
    for ecu_ref, pid, response in non_answers:
        captures.append({"rx": ecu_ref, "pid": pid, "response": response})

    return {"date": date or _today(), "label": label, **meta, "captures": captures}


def build_scan_session(
    ecu_ref: str,
    tx_id: int,
    service: int,
    pid_range: IdRange | Sequence[IdRange],
    positive: list[tuple[int, UdsResponse]],
    negative: list[tuple[int, int, str]],
    errors: list[tuple[int, str]],
    label: str,
    vehicle_states: list,
    notes: str,
    append_bytes: str = "",
    session_flag: bool = False,
    transport: str | None = None,
) -> CaptureSession:
    """Build a capture session dict from scan results (one range or several)."""
    wide_did = identifier_width(service) == 2
    did_fmt = "04X" if wide_did else "02X"

    range_str = format_id_ranges(as_id_ranges(pid_range), 4 if wide_did else 2)
    suffix = f" + suffix {append_bytes}" if append_bytes else ""

    meta = _session_meta(vehicle_states, notes, transport)

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
    return {"date": _today(), "label": label, **meta, "captures": [scan_capture]}


def build_raw_session(
    ecu_ref: str,
    tx_id: int,
    request: str,
    response: UdsResponse,
    label: str,
    vehicle_states: list,
    notes: str,
    pids_data: dict | None = None,
    transport: str | None = None,
) -> CaptureSession:
    """Build a capture session dict from a raw UDS response.

    Decoded parameter values are intentionally NOT stored — they are derived
    data, regenerated on demand from the payload + PID definitions (see
    decode.py and query-captures.py).
    """
    meta = _session_meta(vehicle_states, notes, transport)

    capture: CaptureRecord = {
        "rx": ecu_ref,
        "pid": request,
    }

    if response["ok"]:
        capture["payload"] = response["hex"].upper()
        # Same invariant as build_query_session: a payload capture is a
        # time-series sample, so it must carry a timestamp or it is silently
        # excluded from every time-aligned analysis. The raw path has no caller
        # timestamp to pass through, so it stamps acquisition time here.
        capture["time"] = datetime.now().strftime("%H:%M:%S")
    else:
        # A non-answer (NRC / error) is not a sample and is deliberately untimed.
        capture["response"] = non_answer_text(response)

    return {"date": _today(), "label": label, **meta, "captures": [capture]}


def non_answer_text(response: UdsResponse) -> str:
    """How a capture records a request that drew no payload: its NRC, else the error."""
    if response.get("nrc") is not None:
        return f"NRC 0x{response['nrc']:02X} ({response.get('nrc_desc', '')})"
    return response.get("error") or "unknown error"


def build_discover_session(
    alive: list[tuple[int, int, str, str]],
    silent_count: int,
    error_count: int,
    addr_range: tuple[int, int],
    label: str,
    vehicle_states: list,
    notes: str,
    transport: str | None = None,
) -> CaptureSession:
    """Build a capture session dict from discovery scan results.

    ``alive`` holds one ``(tx_id, rx_id, response, detail)`` per responder. The
    top-level ``rx`` is the ``broadcast`` sentinel (a discovery scan spans many
    ECUs), so each responder records both its own addresses: ``tx`` is the
    request id that was probed — the fact the sweep observed — and ``rx`` the
    response address under the profile's addressing (a registered ECU's
    ``rx_id``, else the profile ``rx_offset``/mode), the same one the sweep
    printed. The caller resolves ``rx_id`` once, so the printed, ``--json`` and
    saved addresses cannot disagree.
    """
    start, end = addr_range
    meta = _session_meta(vehicle_states, notes, transport)

    capture: CaptureRecord = {
        "rx": "broadcast",
        "pid": f"discover {start:03X}-{end:03X}",
    }

    scan_results: ScanResults = {}
    if alive:
        responding: list[RespondingEntry] = []
        for tx_id, rx_id, response, resp_hex in alive:
            entry: RespondingEntry = {
                "tx": f"0x{tx_id:03X}",
                "rx": f"0x{rx_id:03X}",
                "response": response,
                "notes": f"Raw: {resp_hex}" if resp_hex else "",
            }
            responding.append(entry)
        scan_results["responding"] = responding

    total_silent = silent_count + error_count
    if total_silent:
        scan_results["rejected"] = f"{total_silent} addresses silent"

    capture["scan_results"] = scan_results
    return {"date": _today(), "label": label, **meta, "captures": [capture]}


def build_manual_session(
    captures: list[CaptureRecord],
    *,
    label: str,
    date: str | None = None,
    vehicle_states: list[str] | None = None,
    notes: str | None = None,
    transport: str | None = IMPORT_TRANSPORT,
    quality: Quality | None = None,
) -> CaptureSession:
    """Assemble a session dict from manually-supplied captures (the import path).

    Mirrors the shape produced by the ``--save`` streaming path so an imported
    session is indistinguishable from a device-recorded one. ``date`` defaults to
    today; ``vehicle_states``/``notes`` are omitted when empty. ``transport``
    defaults to ``"import"`` (these payloads didn't come off a live bus here), so
    imported data is distinguishable from device-recorded data in provenance.
    ``quality`` is the data-quality footprint of an import that can measure one (a
    passively tapped log counts the frames it lost). Field order follows the schema
    (date, label, [vehicle_states], [notes], [transport], [quality], captures).
    """
    meta = _session_meta(vehicle_states, notes, transport)
    if quality:
        meta["quality"] = quality.copy()
    return {"date": date or _today(), "label": label, **meta, "captures": list(captures)}


def suggest_scan_label(
    ecu_name: str,
    service: int,
    pid_range: IdRange | Sequence[IdRange],
    append_bytes: str = "",
) -> str:
    """Generate a suggested label for a scan session."""
    ranges = format_id_ranges(as_id_ranges(pid_range), 4 if identifier_width(service) == 2 else 2)
    suffix = f" +{append_bytes}" if append_bytes else ""
    return f"Scan {ecu_name} {service:02X} {ranges}{suffix}"


def suggest_raw_label(ecu_name: str, request: str) -> str:
    """Generate a suggested label for a raw request capture."""
    return f"Raw {ecu_name} {request}"


def suggest_discover_label(addr_range: tuple[int, int]) -> str:
    """Generate a suggested label for a discovery scan."""
    start, end = addr_range
    return f"Discovery scan {start:03X}-{end:03X}"
