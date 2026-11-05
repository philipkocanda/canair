"""Multi-DID batching + UDS result-shaping kernel.

The pure (device-free) core shared by the ``multi`` pipeline mode and the
``monitor`` mode: service-22 multi-DID batching state, response splitting, and
building the result/error dicts both modes emit. Kept free of any terminal /
session-manager dependency so both async orchestrators import the same kernel
rather than each other.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from canair.core.eventlog import log_event
from canair.expression.decoding import decode_param_rows
from canair.expression.results import ResultEntry
from canair.uds.describe import decode_uds_response
from canair.uds.multi_did import MULTI_DID_MAX_DEFAULT, split_chain
from canair.uds.response import UdsResponse
from canair.uds.services import READ_DATA_BY_IDENTIFIER, positive_response_sid
from canair.uds.support_map import map_byte_indices

# Consecutive unanswered batches (each with a successful per-DID fallback) before an
# ECU is read singly for the rest of the session; see BatchState.batch_unanswered.
BATCH_UNANSWERED_BEFORE_DISABLE = 3

_POSITIVE_READ = positive_response_sid(READ_DATA_BY_IDENTIFIER)


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


class BatchState:
    """Per-session UDS service-22 multi-DID batching state.

    Multi-DID support is per-ECU (some Hyundai ECUs answer ``22 D1 D2`` with
    ``62 D1 <data> D2 <data>``; others reject it with NRC 0x13). We learn each
    DID's data length from its first single read, batch once all target DIDs
    have known lengths, and permanently disable batching for an ECU that ever
    rejects it (or whose response fails to split, or arrives too late to be
    taken as the batch's answer, or keeps going unanswered while single reads
    succeed) for the rest of the session.
    """

    def __init__(self, pad: int = 0xAA, max_dids: int = MULTI_DID_MAX_DEFAULT):
        self.lengths: dict[tuple[int, str], int] = {}  # (tx_id, DID4) -> data bytes
        self.disabled: set[int] = set()  # tx_ids that don't support batching
        self.pad = pad  # reply padding byte (profile isotp.rx_padding)
        self.max_dids = max_dids  # max DIDs combined per multi-DID request
        self._clamped: set[tuple[int, int]] = set()  # (tx_id, cap) already warned about
        self._unanswered: dict[int, int] = {}  # tx_id -> batches unanswered in a row

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
        dlen = _did_data_len(resp_hex, did4)
        if dlen is not None and dlen >= 1:
            self.lengths[(tx_id, did4.upper())] = dlen

    def batch_answered(self, tx_id: int) -> None:
        """A batch for ``tx_id`` came back split cleanly: its unanswered streak is over."""
        self._unanswered.pop(tx_id, None)

    def batch_unanswered(self, tx_id: int) -> None:
        """A batch went unanswered although single reads of its DIDs succeeded.

        One such miss is ordinary bus noise, but a run of them means the ECU answers batches
        too slowly for the request budget. Each costs a whole group's worse-than-single round
        trip, and a reply that arrives late can land in the fallback read's slot, so after
        :data:`BATCH_UNANSWERED_BEFORE_DISABLE` in a row the ECU is read singly.
        """
        n = self._unanswered.get(tx_id, 0) + 1
        self._unanswered[tx_id] = n
        if n >= BATCH_UNANSWERED_BEFORE_DISABLE:
            self.disable(tx_id, f"{n} multi-DID requests in a row went unanswered")


def _did_data_len(resp_hex: str, did4: str) -> int | None:
    """Length (bytes) of a single-DID ``62 <DID> <data>`` response's data.

    Returns the number of bytes after the 2-byte DID, or None if it doesn't parse.
    The response must be at its exact ISO-TP length, which is what both live
    transports hand back (the raw stack trims to the PCI length, the ELM327 path to
    the adapter's declared length). Nothing is stripped as padding: a reply whose
    data happens to end in the fill byte would be learned short, and every later
    batch would then split it short and take its real last bytes for padding.
    """
    try:
        b = bytes.fromhex(resp_hex)
        did = bytes.fromhex(did4)
    except ValueError:
        return None
    if len(b) < 3 or b[0] != _POSITIVE_READ or b[1:3] != did:
        return None
    return len(b) - 3


def split_multi_did(
    resp_hex: str, dids_lengths: list[tuple[str, int]], pad: int = 0xAA
) -> dict[str, str] | None:
    """Split a ``62`` multi-DID response into per-DID single-style responses.

    Args:
        resp_hex: reassembled UDS payload, ``62 D1 <data1> D2 <data2> … [pad…]``.
        dids_lengths: ordered ``(DID4, data_len_bytes)`` as requested.
        pad: the byte replies are padded with (profile ``isotp.rx_padding``, default 0xAA).

    Returns ``{DID4: "62"+DID+data hex}`` (each looking like a normal single-DID
    response so existing decoders work unchanged), or ``None`` if the response
    doesn't match the expected DIDs/lengths (→ caller falls back to per-DID).
    """
    try:
        b = bytes.fromhex(resp_hex)
    except ValueError:
        return None
    if not b or b[0] != _POSITIVE_READ:
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


def _did4(pid_code: str) -> str:
    """The DID of a service-22 request code, upper-cased (``22bc03`` → ``BC03``).

    Learned lengths are keyed by it, and :func:`canair.uds.multi_did.split_chain` looks
    DIDs up upper-case, so a PID key written in lower case in ``ecus/`` must not
    file its length where the leak check cannot find it.
    """
    return pid_code[2:].upper()


def is_leaked_batch_reply(resp_hex: str, data_len: Callable[[str], int | None]) -> bool:
    """Is this answer to a single-DID read really a whole multi-DID batch reply?

    A batch reply starts ``62 D1``, so when a batch request times out and its reply lands in
    the per-DID fallback read of ``D1``, the echo check passes it. Stored, it became ``D1``'s
    payload with every other DID's data appended, and learned, it poisoned ``D1``'s length for
    every later batch. ``data_len`` gives the session's learned length per DID
    (see :func:`canair.uds.multi_did.split_chain`).
    """
    try:
        payload = bytes.fromhex(resp_hex)
    except ValueError:
        return False
    return split_chain(payload, data_len) is not None


LEAKED_BATCH_ERROR = "multi-DID batch reply received for a single-DID read (discarded)"

# Why batching stops once a leaked reply is seen: the batch it answered had already
# been given up on, so batches on this ECU outlast the request budget.
LEAKED_BATCH_REASON = "a multi-DID reply arrived after its request timed out"


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
    target: int | str | None = None,
) -> ResultEntry:
    """Build a result dict from a successful (single or split-out) response.

    ``elapsed_ms`` (wall-clock round-trip) is carried through to the saved
    capture only for single per-DID reads — batched multi-DID reads share one
    round-trip across several PIDs, so they pass ``None`` and omit the field.

    With the request's ``target`` (its TX id, or the ECU's name), a reply to a
    sensitive PID is masked here, before anything renders or records it
    (:func:`canair.privacy.sensitive.mask_result`).
    """
    entry: ResultEntry
    if pid_info:
        entry = {
            "pid": pid_code,
            "params": decode_param_rows(hex_str, pid_info["parameters"]),
            "raw_hex": hex_str,
            "acquired_at": acquired_at,
        }
        map_bytes = map_byte_indices(pid_info, pid_code)
        if map_bytes:
            entry["map_bytes"] = map_bytes
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
    if target is not None:
        from canair.privacy.sensitive import mask_result

        mask_result(entry, target, pid_info)
    return entry


def _error_result(
    pid_code: str, unmapped: bool, resp: UdsResponse, acquired_at: float | None
) -> ResultEntry:
    entry: ResultEntry = {
        "pid": pid_code,
        "error": resp.get("error") or resp.get("nrc_desc", "unknown"),
        "unmapped": unmapped,
        "acquired_at": acquired_at,
    }
    nrc = resp.get("nrc")
    if nrc is not None:
        entry["error"] = f"NRC 0x{nrc:02X} ({resp['nrc_desc']})"
        entry["nrc"] = nrc
    return entry
