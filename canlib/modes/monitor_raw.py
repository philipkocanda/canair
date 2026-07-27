"""Live monitor — raw-CAN (slcan) polling backend.

:class:`MonitorRawPoller` is the collaborator that owns the monitor's raw-CAN
poll cycle: it plans each cycle's requests (batching a ``multi_did`` ECU's
service-22 DIDs), drives the pipelined blocking client on a worker thread,
consumes results incrementally, and splits/learns multi-DID lengths. It holds
the per-session batch-learning state (``lengths``/``nobatch``).

Factored out of :class:`canlib.modes.monitor.MonitorController` (mirrors
:class:`canlib.modes.monitor_edit.MonitorEditor`) so the raw backend — only
active when the transport is ``slcan-tcp`` — is a self-contained, independently
testable unit rather than another arm of the controller god object. The
controller keeps thin delegating methods for the tested public surface.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .multi_batch import EcuFrame, ResultEntry


def _raw_pid_result(
    pid_code: str,
    pid_info: dict | None,
    unmapped: bool,
    value: bytes | Exception | None,
    acquired_at: float | None,
) -> ResultEntry:
    """Turn a raw-CAN poll result (bytes / Exception / None) into a result dict.

    Mirrors the ELM path's result shape so the renderer/decoder are unchanged.
    """
    from .multi_batch import _decode_pid_result

    if value is None or isinstance(value, Exception):
        err = "timeout" if value is None or isinstance(value, TimeoutError) else str(value)
        return {"pid": pid_code, "error": err, "unmapped": unmapped, "acquired_at": acquired_at}
    resp = bytes(value)
    if not resp:
        return {
            "pid": pid_code,
            "error": "empty response",
            "unmapped": unmapped,
            "acquired_at": acquired_at,
        }
    if resp[0] == 0x7F:  # negative response: 7F <sid> <nrc>
        from ..uds_parse import nrc_abbrev

        nrc = resp[2] if len(resp) >= 3 else 0
        return {
            "pid": pid_code,
            "error": f"NRC 0x{nrc:02X} ({nrc_abbrev(nrc)})",
            "unmapped": unmapped,
            "acquired_at": acquired_at,
        }
    return _decode_pid_result(pid_code, pid_info, unmapped, resp.hex().upper(), resp, acquired_at)


class MonitorRawPoller:
    """Raw-CAN poll backend + per-session multi-DID batching state.

    A collaborator of :class:`~canlib.modes.monitor.MonitorController` (accessed
    as ``controller.raw_poller``); reads/writes the controller's shared live
    state (``last_queries``, ``_last_good``, ``disconnected``, …) through the
    back-reference ``self.c``.
    """

    def __init__(self, controller):
        self.c = controller
        # Learned per-DID data lengths (ecu, did4) -> len, and ECUs that rejected
        # batching (NRC 0x13/0x31 or an unsplittable positive) this session.
        self.lengths: dict[tuple[str, str], int] = {}
        self.nobatch: set[str] = set()

    def build_submissions(self):
        """Plan this cycle's raw requests, batching multi-DID ECUs.

        Returns ``(submissions, plan_by_ecu)``. Each submission is a dict with
        ``ecu``, ``req`` (bytes to send), ``members`` [(pid_code, pid_info,
        unmapped)], and ``lengths`` ([(did4, data_len)] for a batch, else None).
        Consecutive service-22 DIDs on a ``multi_did`` ECU whose lengths are
        already learned are combined (≤3, single-frame request); everything else
        is a single request (and 22-DID lengths are learned from single reads).
        """
        from .multi_batch import _is_did22
        from .multi_exec import build_query_plan

        c = self.c
        # Only reached during polling, after setup() built the ECU index.
        assert c._ecu_index is not None
        submissions: list[dict] = []
        plan_by_ecu: list[tuple[str, int, list]] = []
        for step in c.query_steps:
            ecu = step["ecu"].upper()
            info = c._ecu_index.get(ecu)
            if info is None:
                continue
            plan = (
                build_query_plan(
                    info, step.get("pids", []), quiet=True, include_static=c.include_static
                )
                or []
            )
            plan_by_ecu.append((ecu, info["tx_id"], plan))
            batchable = info.get("multi_did", False) and ecu not in self.nobatch
            i, n = 0, len(plan)
            while i < n:
                code = plan[i][0]
                if batchable and _is_did22(code) and (ecu, code[2:]) in self.lengths:
                    group = []
                    while (
                        i < n
                        and len(group) < 3
                        and _is_did22(plan[i][0])
                        and (ecu, plan[i][0][2:]) in self.lengths
                    ):
                        group.append(plan[i])
                        i += 1
                    if len(group) > 1:
                        dids = [g[0][2:] for g in group]
                        submissions.append(
                            {
                                "ecu": ecu,
                                "req": bytes.fromhex("22" + "".join(dids)),
                                "members": group,
                                "lengths": [(d, self.lengths[(ecu, d)]) for d in dids],
                            }
                        )
                        continue
                    g = group[0]
                    submissions.append(
                        {"ecu": ecu, "req": bytes.fromhex(g[0]), "members": [g], "lengths": None}
                    )
                    continue
                submissions.append(
                    {"ecu": ecu, "req": bytes.fromhex(code), "members": [plan[i]], "lengths": None}
                )
                i += 1
        return submissions, plan_by_ecu

    async def poll(self) -> None:
        """Pipelined UDS read over raw CAN (blocking client run in a thread).

        Multi-DID ECUs are batched (one ISO-TP request per group); results are
        split back per-DID. Per-ECU 22-DID lengths are learned from single reads,
        and an ECU that rejects batching (NRC 0x13/0x31) or returns an
        unsplittable response is dropped to single reads for the session.

        Results are consumed **incrementally**: the client fires a callback as
        each request resolves, so fast PIDs render immediately and a slow/timing-
        out PID only holds up its own row — the view never freezes for a cycle.
        """
        import time as _t

        c = self.c
        # The raw poll path only runs on the raw backend, where raw_client is set.
        assert c.raw_client is not None
        raw_client = c.raw_client  # local binding: narrowing doesn't reach the closure below
        submissions, plan_by_ecu = self.build_submissions()
        requests = [(s["ecu"], s["req"]) for s in submissions]
        sub_by_req = {(s["ecu"], s["req"]): s for s in submissions}

        loop = asyncio.get_event_loop()
        q: asyncio.Queue = asyncio.Queue()
        sentinel = object()

        def _on_result(key, value):  # fired from the executor thread
            loop.call_soon_threadsafe(q.put_nowait, (key, value))

        def _run():
            try:
                return raw_client.poll(requests, on_result=_on_result)
            finally:
                loop.call_soon_threadsafe(q.put_nowait, sentinel)

        fut = loop.run_in_executor(None, _run)

        by_pid: dict[tuple[str, str], ResultEntry] = {}
        applied: set = set()
        last_render = 0.0
        while True:
            item = await q.get()
            if item is sentinel:
                break
            key, val = item
            s = sub_by_req.get(key)
            if s is None:
                continue
            self.apply_submission(s, val, _t.time(), by_pid)
            applied.add(key)
            c.last_queries = self.build_queries(plan_by_ecu, by_pid)
            # Throttle mid-cycle repaints so a burst of fast PIDs doesn't thrash.
            now = _t.monotonic()
            if c._on_partial is not None and (now - last_render) >= 0.12:
                last_render = now
                with contextlib.suppress(Exception):
                    c._on_partial()
        try:
            result_dict = await fut  # surface a bus-level failure (connection dropped)
        except Exception:
            c.disconnected = True
            return

        # Completeness net: fold in any results not delivered via the callback
        # (a client may return the dict without implementing on_result).
        for s in submissions:
            key = (s["ecu"], s["req"])
            if key not in applied and key in result_dict:
                self.apply_submission(s, result_dict[key], _t.time(), by_pid)

        c.last_queries = self.build_queries(plan_by_ecu, by_pid)
        c.last_cmds = len(requests)
        c.last_elm_time = 0.0

    def apply_submission(
        self, s: dict, val, acquired: float, by_pid: dict[tuple[str, str], ResultEntry]
    ) -> None:
        """Fold one completed submission's response into ``by_pid`` (split batches,
        learn 22-DID lengths, track ECUs that can't batch)."""
        from .multi_batch import _did_data_len, _is_did22, split_multi_did

        ecu = s["ecu"]
        resp = bytes(val) if isinstance(val, (bytes, bytearray)) else None

        if s["lengths"] is not None:  # batched request
            split = None
            if resp and resp[0] != 0x7F:
                split = split_multi_did(resp.hex().upper(), s["lengths"])
            elif resp and resp[0] == 0x7F and (resp[2] if len(resp) >= 3 else 0) in (0x13, 0x31):
                self.nobatch.add(ecu)  # ECU can't batch — fall back next cycle
            if split is None:
                if resp and resp[0] != 0x7F:
                    self.nobatch.add(ecu)  # positive but unsplittable
                for code, pi, un in s["members"]:
                    by_pid[(ecu, code)] = _raw_pid_result(
                        code, pi, un, val if resp is None else resp, acquired
                    )
            else:
                for code, pi, un in s["members"]:
                    sub = bytes.fromhex(split[code[2:]])
                    by_pid[(ecu, code)] = _raw_pid_result(code, pi, un, sub, acquired)
            return

        code, pi, un = s["members"][0]
        by_pid[(ecu, code)] = _raw_pid_result(code, pi, un, val, acquired)
        if _is_did22(code) and resp and resp[0] != 0x7F:  # learn length for batching
            dlen = _did_data_len(resp.hex().upper(), code[2:])
            if dlen is not None:
                self.lengths[(ecu, code[2:])] = dlen

    def build_queries(
        self, plan_by_ecu, by_pid: dict[tuple[str, str], ResultEntry]
    ) -> list[EcuFrame]:
        """Build the render frame in plan order. A PID resolved as a timeout keeps
        its last-good values (stale/dimmed); a PID not yet resolved this cycle
        shows its last-good values so the view neither flickers nor stutters."""
        c = self.c
        new_queries: list[EcuFrame] = []
        for ecu, tx_id, plan in plan_by_ecu:
            label = f"{ecu} (0x{tx_id:03X})"
            pid_results = []
            for cd, _pi, _un in plan:
                entry = by_pid.get((ecu, cd))
                if entry is None:  # pending this cycle → show last good if any
                    last = c._last_good.get((label, cd))
                    if last is not None:
                        pid_results.append(last)
                    continue
                pid_results.append(c._displayify((label, cd), entry))
            new_queries.append((label, pid_results))
        return new_queries
