"""Single and batched PID reads: one request, its reply shaped into a result entry.

The device-talking primitives under :mod:`canair.live.read.exec`: :func:`timed_exchange`
(one request, timed), :func:`_read_single` (one PID) and :func:`_read_batch` (one multi-DID
request). The query plan that chooses between them stays in ``exec``.
"""

import logging
import time
from typing import Literal

from canair.core.eventlog import log_event
from canair.expression.results import ResultEntry
from canair.profile.model import Profile
from canair.uds.echo import request_echo
from canair.uds.multi_did import BATCH_REFUSAL_NRCS
from canair.uds.response import UdsResponse
from canair.uds.services import READ_DATA_BY_IDENTIFIER

from .batch import (
    LEAKED_BATCH_ERROR,
    LEAKED_BATCH_REASON,
    _decode_pid_result,
    _did4,
    _error_result,
    _is_did22,
    is_leaked_batch_reply,
    split_multi_did,
)


async def timed_exchange(
    sm, tx_id: int, request: str, **kwargs
) -> tuple[UdsResponse, float, float]:
    """Send ``request`` to ``tx_id``: its reply, wall seconds, and seconds it held the pipe.

    set_header + send_uds run inside one transaction, so a background keepalive can't
    re-point the ELM header between them and send this request to the wrong ECU. A
    keepalive queued ahead of it on the serial pipe still delays it, so the time spent on
    keepalives meanwhile is no part of what this request cost (the monitor's backoff
    must not slow a PID for another ECU's session upkeep).
    """
    keepalive0 = sm.keepalive_seconds
    t0 = time.monotonic()
    async with sm.terminal.transaction():
        await sm.terminal.set_header(tx_id)
        resp = await sm.terminal.send_uds(request, **kwargs)
    wall = time.monotonic() - t0
    return resp, wall, max(0.0, wall - (sm.keepalive_seconds - keepalive0))


async def _read_single(sm, tx_id, pid_code, pid_info, unmapped, batch_state, *, profile: Profile):
    """Send one PID, return its result dict; learn its 22-DID length for batching.

    keepalive_stale runs *outside* the request's transaction (it is unrelated traffic
    that shouldn't be serialised behind this exchange); see :func:`timed_exchange`.
    """
    await sm.keepalive_stale()
    echo = request_echo(pid_code)
    expected_sid = echo[0] if echo else None
    expected_echo = echo[1] if echo else None
    resp, wall, request_s = await timed_exchange(
        sm, tx_id, pid_code, retries=1, expected_sid=expected_sid, expected_echo=expected_echo
    )
    # Wall-clock round-trip (transport + ECU) of this single read, persisted on the capture as
    # a relative timing signal. A batched read gets none (one round-trip answers several PIDs).
    entry = _single_result(
        sm,
        tx_id,
        pid_code,
        pid_info,
        unmapped,
        batch_state,
        resp,
        round(wall * 1000),
        profile=profile,
    )
    entry["request_s"] = request_s
    return entry


def _single_result(
    sm,
    tx_id,
    pid_code,
    pid_info,
    unmapped,
    batch_state,
    resp: UdsResponse,
    elapsed_ms: int,
    *,
    profile: Profile,
) -> ResultEntry:
    """Shape one single read's reply (see :func:`_read_single`)."""
    # Timestamp the moment the response arrived so sequentially-polled PIDs keep
    # their true sub-second acquisition skew.
    acquired_at = time.time()
    if resp.get("ok") or resp.get("nrc") is not None:
        sm.mark_active(tx_id)  # a real answer resets S3 — no redundant 3E00 needed
    if not resp.get("ok"):
        return _error_result(pid_code, unmapped, resp, acquired_at)
    if (
        batch_state is not None
        and _is_did22(pid_code)
        and is_leaked_batch_reply(
            resp.get("hex", ""), lambda d: batch_state.lengths.get((tx_id, d))
        )
    ):
        # Checked before learn(): taking it would store the batch as this DID's payload and
        # set this DID's length to the whole batch's, so every later batch fails to split.
        # The batch it answers was already given up on, so batching this ECU would repeat that
        # every cycle. This read's own reply may still be on its way: the next read's echo check
        # then catches it as stale (the ELM327 engine realigns its pipe before retrying; the raw
        # path flushes its receive queue before every request).
        log_event(
            "stale", LEAKED_BATCH_ERROR, level=logging.INFO, ecu=f"0x{tx_id:03X}", pid=pid_code
        )
        batch_state.disable(tx_id, LEAKED_BATCH_REASON)
        return {
            "pid": pid_code,
            "error": LEAKED_BATCH_ERROR,
            "unmapped": unmapped,
            "acquired_at": acquired_at,
        }
    if batch_state is not None and _is_did22(pid_code) and resp.get("hex"):
        batch_state.learn(tx_id, _did4(pid_code), resp["hex"])
    return _decode_pid_result(
        pid_code,
        pid_info,
        unmapped,
        resp["hex"],
        resp["bytes"],
        acquired_at,
        elapsed_ms,
        tx_id,
        profile=profile,
    )


BatchOutcome = Literal["ok", "failed", "unanswered"]


async def _read_batch(sm, tx_id, group, out, batch_state, *, profile: Profile) -> BatchOutcome:
    """Attempt one multi-DID request for ``group``; append results on success.

    Returns ``"ok"`` if the batch succeeded and split cleanly. On a refusal in
    ``BATCH_REFUSAL_NRCS`` or an unsplittable response, it disables batching for the ECU for
    good and returns ``"failed"``, so the caller falls back to per-DID reads. A non-answer
    (e.g. NO DATA) returns ``"unanswered"`` without disabling; the caller decides, from the
    fallback, whether it counts against the ECU.
    """
    dids = [_did4(e[0]) for e in group]
    await sm.keepalive_stale()
    # Validated like the single-PID path: a 62 response repeats the first requested DID, so a
    # leaked reply classifies as `stale` (pipe resync) instead of failing split_multi_did
    # silently, which the caller reads as "unsplittable" and disables batching for the ECU.
    resp, _wall, request_s = await timed_exchange(
        sm,
        tx_id,
        "22" + "".join(dids),
        expected_sid=READ_DATA_BY_IDENTIFIER,
        expected_echo=bytes.fromhex(dids[0]) if dids else None,
    )
    acquired_at = time.time()
    if resp.get("ok") or resp.get("nrc") is not None:
        sm.mark_active(tx_id)  # a real answer resets S3 — no redundant 3E00 needed
    if not resp.get("ok"):
        if resp.get("nrc") in BATCH_REFUSAL_NRCS:
            batch_state.disable(tx_id, f"rejected a {len(dids)}-DID request with NRC 0x13/0x31")
        return "failed" if resp.get("nrc") is not None else "unanswered"
    split = split_multi_did(
        resp.get("hex", ""),
        [(d, batch_state.lengths[(tx_id, d)]) for d in dids],
        batch_state.pad,
    )
    if split is None:
        batch_state.disable(tx_id, "multi-DID response did not split into the requested DIDs")
        return "failed"
    batch_state.batch_answered(tx_id)
    for pid_code, pid_info, unmapped in group:
        sub_hex = split[_did4(pid_code)]
        entry = _decode_pid_result(
            pid_code,
            pid_info,
            unmapped,
            sub_hex,
            bytes.fromhex(sub_hex),
            acquired_at,
            target=tx_id,
            profile=profile,
        )
        # Each member was answered by the one request, so each was held up that long.
        entry["request_s"] = request_s
        out.append(entry)
    return "ok"
