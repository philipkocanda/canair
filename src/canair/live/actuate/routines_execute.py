"""``canair routines ECU --rid RID``: send one RoutineControl sub-function and print the reply.

``canair routines`` builds UDS RoutineControl requests, ``31 {type} {RID} {option_record}``.
A KWP2000 ECU reads the same bytes as StartRoutineByLocalIdentifier: ``31 03 00 05`` starts
routine local identifier ``0x03`` with the entry options ``00 05``. So the "safe" results request
would start a routine. The blocklist does not refuse it, because on KWP2000 every ``0x31`` request
is a routine start, which ``raw`` and ``read`` may send on purpose.
:func:`check_routines_protocol` refuses such an ECU instead, for this run and for the routines TUI.

An ECU counts as KWP2000 when its ``id_protocol`` says so, judged by the same
:class:`~canair.safety.blocklist.SessionPolicy` the transports use, or when its ``routines:``
section holds a key shorter than a UDS routine id: ``canair scan routines`` writes the local
identifier its KWP2000 ``0x33`` probe found as two digits.
"""

from __future__ import annotations

import json
import sys

from canair.ecus.pids import RoutineIndexEntry, build_routines_index
from canair.live.actuate.not_found import report_not_found
from canair.render import ansi, glyphs, messages
from canair.render.paint import Painter
from canair.safety.blocklist import SafetyBlocked, SessionPolicy
from canair.transport.protocol import Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.echo import request_echo
from canair.uds.protocol import KWP
from canair.uds.requests import routine_control_request
from canair.uds.response import UdsResponse
from canair.uds.services import ROUTINE_CONTROL, identifier_width
from canair.uds.subfunctions import RoutineControlType, subfunction_name

_UDS_ROUTINE_ID_DIGITS = identifier_width(ROUTINE_CONTROL) * 2
DEFAULT_ROUTINE_TIMEOUT_S = 3.0


def sf_label(sf: int) -> str:
    return subfunction_name(ROUTINE_CONTROL, sf) or f"0x{sf:02X}"


def check_routine_request_size(option_record: bytes, max_request_bytes: int) -> None:
    """Refuse a routine option record the transport cannot send in one exchange.

    Use the request builder to count the fixed fields, so their layout has one owner. This
    check runs before a session is opened, and before the CLI asks to start a routine.
    """
    request = routine_control_request(RoutineControlType.REQUEST_ROUTINE_RESULTS, 0, option_record)
    size = len(request) // 2
    if size > max_request_bytes:
        raise ValueError(
            f"RoutineControl request is {size} bytes; this transport allows at most "
            f"{max_request_bytes} bytes"
        )


def routines_refusal(pids_data: dict, ecu_name: str) -> str | None:
    """Why ``canair routines`` must not address ``ecu_name``, or None.

    None also for an ECU with no ``routines:`` section, which the caller reports as not found.
    """
    ecu = ecu_name.upper()
    entry = build_routines_index(pids_data).get(ecu)
    reason = None if entry is None else _kwp_reason(pids_data, entry)
    if reason is None:
        return None
    return (
        f"{ecu} {reason}\n"
        "canair routines sends UDS RoutineControl (31 …), which a KWP2000 ECU reads as\n"
        "StartRoutineByLocalIdentifier and runs. Read a KWP2000 routine's results with\n"
        f"`canair scan routines {ecu} --rid-range LID-LID` (service 0x33)"
    )


def check_routines_protocol(pids_data: dict, ecu_name: str) -> None:
    """Raise :class:`SafetyBlocked` when :func:`routines_refusal` refuses ``ecu_name``."""
    reason = routines_refusal(pids_data, ecu_name)
    if reason is not None:
        raise SafetyBlocked(reason)


def _kwp_reason(pids_data: dict, entry: RoutineIndexEntry) -> str | None:
    if SessionPolicy.from_profile(pids_data).protocol(entry["tx_id"]) == KWP:
        return "speaks KWP2000"
    local_ids = sorted(rid for rid in entry["routines"] if len(rid) < _UDS_ROUTINE_ID_DIGITS)
    if local_ids:
        return f"lists KWP2000 local identifiers under routines: ({', '.join(local_ids)})"
    return None


async def mode_routines_execute(
    terminal: Terminal,
    pids_data: dict,
    ecu_name: str,
    rid: str,
    sub_function: int = RoutineControlType.REQUEST_ROUTINE_RESULTS,
    verbose: bool = False,
    as_json: bool = False,
    *,
    option_record: bytes = b"",
    timeout: float = DEFAULT_ROUTINE_TIMEOUT_S,
) -> bool:
    """Execute a RoutineControl sub-function on a specific RID.

    Defaults to REQUEST_ROUTINE_RESULTS (0x03, read-only). START_ROUTINE sends startRoutine;
    the caller is responsible for confirming that is intentional. A lookup that finds nothing is
    ``error:`` on stderr and exit 1. With ``as_json`` stdout holds only the JSON document: the
    report of what was sent goes to stderr. ``option_record`` is the opaque, routine-specific
    bytes to append; a request the transport cannot carry raises ``ValueError`` before the session.
    ``timeout`` is the overall wait for the routine's response, including request transmission.
    Returns whether the routine request succeeded, after stopping the session keepalive.
    """
    stream = sys.stderr if as_json else sys.stdout
    paint = Painter(stream)

    def say(text: str = "") -> None:
        print(text, file=stream)

    rindex = build_routines_index(pids_data)
    ecu_key = ecu_name.upper()
    rid_key = rid.upper()

    if ecu_key not in rindex:
        report_not_found(f"no routines for ECU: {ecu_name}", rindex.keys(), "ECUs with routines")
        raise SystemExit(1)
    check_routines_protocol(pids_data, ecu_key)

    ecu_info = rindex[ecu_key]
    routines = ecu_info["routines"]

    if rid_key not in routines:
        report_not_found(f"unknown RID {rid_key} for {ecu_key}", routines.keys(), "available RIDs")
        raise SystemExit(1)

    rdef = routines[rid_key]
    tx_id = ecu_info["tx_id"]
    sf_name = sf_label(sub_function)
    label = rdef["label"] or rid_key

    check_routine_request_size(option_record, terminal.max_request_bytes)
    hex_cmd = routine_control_request(sub_function, int(rid_key, 16), option_record)

    say(
        f"\n  {paint(ecu_key, ansi.raw.BOLD + ansi.raw.CYAN)} 0x{tx_id:03X} — "
        f"{label} ({paint(rid_key, ansi.raw.CYAN)}) — {sf_name}"
    )
    say(f"  Command: {hex_cmd}")

    async with SessionManager(terminal, verbose=verbose).held(tx_id):
        async with terminal.transaction():
            await terminal.set_header(tx_id)
            response = await send_routine(terminal, tx_id, hex_cmd, timeout=timeout)

        if response["ok"]:
            say(f"  {paint(glyphs.OK, ansi.raw.GREEN)} Positive response: {response['hex']}")
        elif response.get("nrc") is not None:
            nrc = response["nrc"]
            desc = response.get("nrc_desc") or "unknown negative response"
            messages.error(f"{ecu_key} routine {rid_key}: NRC 0x{nrc:02X}: {desc}")
        else:
            messages.error(
                f"{ecu_key} routine {rid_key}: {response.get('error') or 'unknown error'}"
            )

        if as_json:
            out = {
                "ecu": ecu_key,
                "rid": rid_key,
                "label": label,
                "sub_function": sf_name,
                "command": hex_cmd,
                "ok": response["ok"],
                "response": response["hex"] if response["ok"] else None,
                "nrc": f"0x{response['nrc']:02X}" if response.get("nrc") is not None else None,
            }
            print(json.dumps(out, indent=2))

    say()
    return response["ok"]


async def send_routine(
    terminal: Terminal, tx_id: int, request: str, *, timeout: float = DEFAULT_ROUTINE_TIMEOUT_S
) -> UdsResponse:
    """Send a RoutineControl ``request`` to ``tx_id``, checked against the reply it must carry.

    The reply must answer RoutineControl and, on an ECU known to speak UDS, repeat the
    control type and the routine id. A late reply to an earlier request then reads as
    stale instead of as this routine's result.
    """
    expected = request_echo(request, terminal.session_policy.protocol(tx_id))
    return await terminal.send_uds(
        request,
        timeout=timeout,
        expected_sid=ROUTINE_CONTROL,
        expected_echo=expected[1] if expected is not None else None,
    )
