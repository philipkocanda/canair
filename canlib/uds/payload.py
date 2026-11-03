"""Checks on a reply payload held as a hex string — a stored capture or a raw frame.

Both are soft judgements used by ``canair validate captures``, ``canair import uds``,
``canair coverage`` and the pipelined raw client: they report a reason string, or
``None`` when nothing is wrong or nothing can be checked.
"""

from __future__ import annotations

from canlib.uds.echo import echo_mismatch, hex_bytes, request_echo
from canlib.uds.services import NEGATIVE_RESPONSE_SID


def payload_echo_mismatch(request_pid: str, payload_hex: str) -> str | None:
    """Cross-check a reply payload against the PID it was recorded for.

    Returns a human-readable reason when the payload's SID+identifier echo does NOT
    match ``request_pid`` (e.g. a ``6101…`` payload filed under a ``2102`` request —
    the ELM327 stale-frame bug), or ``None`` when it matches or when we can't
    validate (non-identifier request, NRC, hex we can't parse). Only known echo
    mismatches are reported, so this is safe to surface as a soft lint warning.
    """
    echo = request_echo(request_pid)
    if echo is None:
        return None  # not a plain identifier read — nothing to check
    resp = hex_bytes(payload_hex)
    if resp is None or resp[0] == NEGATIVE_RESPONSE_SID:
        return None  # unparseable, or a negative response — leave to other checks
    expected_sid, expected_id = echo
    problem = echo_mismatch(expected_sid, expected_id, resp)
    if problem is None:
        return None
    if problem.field == "sid":
        return (
            f"payload SID 0x{resp[0]:02X} != expected 0x{problem.expected.hex().upper()} "
            f"for request {request_pid}"
        )
    if problem.field == "length":
        return (
            f"payload too short to echo id 0x{expected_id.hex().upper()} "
            f"for request {request_pid} (stale/misfiled frame?)"
        )
    return (
        f"payload echoes id 0x{problem.got.hex().upper()} but request {request_pid} "
        f"expects 0x{expected_id.hex().upper()} (stale/misfiled frame?)"
    )


def payload_not_hex(payload: str) -> str | None:
    """Reason a stored capture payload is not a valid UDS byte string, else None.

    A capture payload should be an even-length run of hex digits (the raw
    response bytes). Anything else is a mis-recorded capture: an ELM327/status
    string kept verbatim (``NO DATA``, ``CAN ERROR``, ``5001 at attempts …``),
    free-text notes, or a mixed hex+ASCII transcription (e.g. a part-number
    ``62F18791950G7510`` where the ``G`` breaks pure-hex). Reported as a soft
    lint warning, never an error — payloads are recorded by the tool, so a
    non-hex one signals a bug/manual edit rather than a schema violation.
    """
    if not payload:
        return None
    cleaned = str(payload).replace(" ", "").strip()
    if len(cleaned) < 2:
        return f"payload too short to be a UDS response: {payload!r}"
    bad = {c for c in cleaned.upper() if c not in "0123456789ABCDEF"}
    if bad:
        return f"payload is not hex (contains {''.join(sorted(bad))!r}): {str(payload)[:40]!r}"
    if len(cleaned) % 2 != 0:
        return f"payload has an odd hex length ({len(cleaned)} nibbles): {str(payload)[:40]!r}"
    return None
