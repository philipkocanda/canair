"""Multi-DID batching + UDS result-shaping kernel.

The pure (device-free) core shared by the ``multi`` pipeline mode and the
``monitor`` mode: service-22 multi-DID batching state, response splitting, and
building the result/error dicts both modes emit. Kept free of any terminal /
session-manager dependency so both async orchestrators import the same kernel
rather than each other.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import NotRequired, TypedDict

from ..decoding import ParamRow, decode_param_rows
from ..formatting import decode_uds_response
from ..log import log_event
from ..uds_parse import UdsResponse


class ResultEntry(TypedDict):
    """One PID's shaped poll result, as emitted by the multi/monitor pipeline.

    The single dict shape shared across the live monitor renderer
    (:mod:`canlib.modes._monitor_render`), the compact printer
    (:func:`canlib.formatting.print_ecu_results`), the capture collectors
    (:mod:`canlib.modes.multi`), and state auto-suggestion
    (:func:`canlib.states.collect_values`). ``pid`` is always present; the rest
    depend on the outcome — a successful decode carries ``params``/``raw_hex``,
    an unmapped PID adds ``decode``/``unmapped``, a failed query carries
    ``error``, and the monitor tags a timed-out-but-retained row ``stale``.
    """

    pid: str
    params: NotRequired[list[ParamRow]]
    raw_hex: NotRequired[str]
    acquired_at: NotRequired[float | None]
    elapsed_ms: NotRequired[int]
    decode: NotRequired[str | None]
    unmapped: NotRequired[bool]
    error: NotRequired[str]
    stale: NotRequired[bool]


# One render/collect frame: per-ECU ``(label, results)`` pairs, where ``label``
# is e.g. ``"BMS (0x7E4)"`` and ``results`` is that ECU's PID entries in order.
EcuFrame = tuple[str, list[ResultEntry]]


# Max service-22 DIDs to combine into one multi-DID request when an ECU opts into
# batching. Bounded because a batch is only as fast as its slowest member (one
# stalled DID holds up the whole group) and a very large group grows the ISO-TP
# response. 3 is deliberately conservative — it keeps the *request* within a
# single CAN frame (``22`` + 3 two-byte DIDs = 7 data bytes); larger groups make
# the request itself multi-frame, which the ECUs tested tolerate but which adds
# request-side flow-control. Override per profile with ``multi_did_max`` (or
# per-ECU with the same key); resolved by :func:`resolve_multi_did_max`.
MULTI_DID_MAX_DEFAULT = 3


def transport_did_cap(max_request_bytes: int) -> int:
    """How many 2-byte DIDs fit in one ``22 D1 D2 …`` request on this transport.

    The request is ``22`` plus two bytes per DID, so ``max_request_bytes`` bounds
    the group size. This is a *hard* ceiling, not a preference: an ELM327 fits 7
    data bytes (⇒ 3 DIDs) and answers an over-long request with a bare ``?``,
    which carries no NRC and so reads as "batching unsupported" — permanently
    demoting the ECU to per-DID reads for the rest of the session. A profile
    tuned on ``slcan-tcp`` (where the client segments) would otherwise lose
    batching entirely the moment it ran over ``wican-ws``.
    """
    return max(1, (max_request_bytes - 1) // 2)


def resolve_multi_did_max(pids_data: dict | None, ecu_def: dict | None = None) -> int:
    """Resolve the multi-DID batch size cap (per-ECU → profile → default).

    ``multi_did_max`` may be set profile-wide (top level of the profile data) and
    overridden per ECU (in the ECU's definition), mirroring how ``multi_did`` /
    ``multi_did_batching`` resolve. A non-positive or missing value falls back to
    :data:`MULTI_DID_MAX_DEFAULT`; a value < 1 is clamped to 1 (batching off).
    """
    val = None
    if ecu_def is not None and "multi_did_max" in ecu_def:
        val = ecu_def["multi_did_max"]
    elif pids_data is not None:
        val = pids_data.get("multi_did_max")
    if not isinstance(val, int) or val < 1:
        return MULTI_DID_MAX_DEFAULT
    return val


class BatchState:
    """Per-session UDS service-22 multi-DID batching state.

    Multi-DID support is per-ECU (some Hyundai ECUs answer ``22 D1 D2`` with
    ``62 D1 <data> D2 <data>``; others reject it with NRC 0x13). We learn each
    DID's data length from its first single read, batch once all target DIDs
    have known lengths, and permanently disable batching for an ECU that ever
    rejects it (or whose response fails to split) for the rest of the session.
    """

    def __init__(self, pad: int = 0xAA, max_dids: int = MULTI_DID_MAX_DEFAULT):
        self.lengths: dict[tuple[int, str], int] = {}  # (tx_id, DID4) -> data bytes
        self.disabled: set[int] = set()  # tx_ids that don't support batching
        self.pad = pad  # ISO-TP padding byte (profile isotp.tx_padding)
        self.max_dids = max_dids  # max DIDs combined per multi-DID request
        self._clamped: set[tuple[int, int]] = set()  # (tx_id, cap) already warned about

    def note_clamp(self, tx_id: int, cap: int) -> bool:
        """Register a transport clamp for ``tx_id``; True the first time only.

        Keeps the "your ``multi_did_max`` doesn't fit this transport" notice to one
        line per ECU instead of one per poll cycle.
        """
        key = (tx_id, cap)
        if key in self._clamped:
            return False
        self._clamped.add(key)
        return True

    def disable(self, tx_id: int, reason: str) -> None:
        """Permanently stop batching ``tx_id`` for this session, and say why.

        Irreversible by design (an ECU that rejects a multi-DID request once will
        reject it every time), which is exactly why it must not be silent: the
        symptom is a poll cycle that quietly takes several times longer, with
        nothing in the output to explain it.
        """
        if tx_id in self.disabled:
            return
        self.disabled.add(tx_id)
        log_event(
            "config",
            f"multi-DID batching disabled for 0x{tx_id:03X} for this session: {reason}",
            level=logging.INFO,
            ecu=f"0x{tx_id:03X}",
        )

    def learn(self, tx_id: int, did4: str, resp_hex: str) -> None:
        """Record a DID's data length from a single-DID ``62 DID <data>`` response."""
        dlen = _did_data_len(resp_hex, did4, self.pad)
        if dlen is not None:
            self.lengths[(tx_id, did4.upper())] = dlen


def _strip_trailing_padding(data: bytes, pad: int = 0xAA) -> bytes:
    """Drop trailing ISO-TP padding bytes (profile ``isotp.tx_padding``, default 0xAA)."""
    i = len(data)
    while i > 0 and data[i - 1] == pad:
        i -= 1
    return data[:i]


def _did_data_len(resp_hex: str, did4: str, pad: int = 0xAA) -> int | None:
    """Length (bytes) of a single-DID response's data, padding stripped.

    ``resp_hex`` is a ``62 <DID> <data> [pad…]`` positive response. Returns the
    number of data bytes after the 2-byte DID, or None if it doesn't parse.
    """
    try:
        b = bytes.fromhex(resp_hex)
        did = bytes.fromhex(did4)
    except ValueError:
        return None
    if len(b) < 3 or b[0] != 0x62 or b[1:3] != did:
        return None
    return len(_strip_trailing_padding(b[3:], pad))


def split_multi_did(
    resp_hex: str, dids_lengths: list[tuple[str, int]], pad: int = 0xAA
) -> dict[str, str] | None:
    """Split a ``62`` multi-DID response into per-DID single-style responses.

    Args:
        resp_hex: reassembled UDS payload, ``62 D1 <data1> D2 <data2> … [pad…]``.
        dids_lengths: ordered ``(DID4, data_len_bytes)`` as requested.
        pad: ISO-TP padding byte (profile ``isotp.tx_padding``, default 0xAA).

    Returns ``{DID4: "62"+DID+data hex}`` (each looking like a normal single-DID
    response so existing decoders work unchanged), or ``None`` if the response
    doesn't match the expected DIDs/lengths (→ caller falls back to per-DID).
    """
    try:
        b = bytes.fromhex(resp_hex)
    except ValueError:
        return None
    if not b or b[0] != 0x62:
        return None
    pos = 1
    out: dict[str, str] = {}
    for did4, dlen in dids_lengths:
        try:
            did = bytes.fromhex(did4)
        except ValueError:
            return None
        if b[pos : pos + 2] != did or len(did) != 2:
            return None
        pos += 2
        data = b[pos : pos + dlen]
        if len(data) != dlen:
            return None
        pos += dlen
        out[did4.upper()] = (b"\x62" + did + data).hex().upper()
    # Anything left over must be padding only.
    if any(x != pad for x in b[pos:]):
        return None
    return out


def _is_did22(pid_code: str) -> bool:
    """True for a full 6-char service-22 DID request like ``22BC03``."""
    return len(pid_code) == 6 and pid_code[:2] == "22"


def _capture_stamp(acquired_at: float | None) -> tuple[str, str]:
    """Split an acquisition epoch into ``(date, time)`` for a saved capture.

    ``time`` keeps millisecond precision (``HH:MM:SS.fff``) so sequentially
    polled PIDs retain their true sub-second skew — the skew cross-signal
    correlate/hunt/--corr rely on. ``date`` is the acquisition date, so a
    session spanning midnight reconciles into the correct per-day files. Falls
    back to "now" when no timestamp is available.
    """
    dt = datetime.fromtimestamp(acquired_at) if acquired_at else datetime.now()
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S.%f")[:-3]


def _decode_pid_result(
    pid_code: str,
    pid_info: dict | None,
    unmapped: bool,
    hex_str: str,
    bytes_val: bytes,
    acquired_at: float | None,
    elapsed_ms: int | None = None,
) -> ResultEntry:
    """Build a result dict from a successful (single or split-out) response.

    ``elapsed_ms`` (wall-clock round-trip) is carried through to the saved
    capture only for single per-DID reads — batched multi-DID reads share one
    round-trip across several PIDs, so they pass ``None`` and omit the field.
    """
    entry: ResultEntry
    if pid_info:
        entry = {
            "pid": pid_code,
            "params": decode_param_rows(hex_str, pid_info["parameters"]),
            "raw_hex": hex_str,
            "acquired_at": acquired_at,
        }
    else:
        entry = {
            "pid": pid_code,
            "params": [],
            "raw_hex": hex_str,
            "decode": decode_uds_response(bytes_val),
            "unmapped": True,
            "acquired_at": acquired_at,
        }
    if elapsed_ms is not None:
        entry["elapsed_ms"] = elapsed_ms
    return entry


def _error_result(
    pid_code: str, unmapped: bool, resp: UdsResponse, acquired_at: float | None
) -> ResultEntry:
    error = resp.get("error") or resp.get("nrc_desc", "unknown")
    nrc = resp.get("nrc")
    if nrc is not None:
        error = f"NRC 0x{nrc:02X} ({resp['nrc_desc']})"
    return {"pid": pid_code, "error": error, "unmapped": unmapped, "acquired_at": acquired_at}
