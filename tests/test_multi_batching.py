"""Tests for UDS service-22 multi-DID batching (canlib.modes.multi).

Uses the real IGPM (0x770) responses captured on-device:
    22BC03      -> 62BC03 FDEE3C730A000000 (padded AAAA)
    22BC06      -> 62BC06 B480000000000000 (padded AAAA)
    22BC03BC06  -> 62BC03 FDEE3C730A000000 BC06 B480000000000000 (padded AA…)
BCM rejects multi-DID with 7F2213 (NRC 0x13).
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from canlib.modes.multi import (
    _exec_query,
    _read_single,
)
from canlib.modes.multi_batch import (
    BATCH_UNANSWERED_BEFORE_DISABLE,
    LEAKED_BATCH_ERROR,
    BatchState,
    _did_data_len,
    is_leaked_batch_reply,
    resolve_multi_did_max,
    split_multi_did,
    transport_did_cap,
)
from canlib.transport.isotp_params import ISOTP_MAX_REQUEST_BYTES
from tests._fakes import FakeTerminal
from tests._fakes import nrc as _nrc
from tests._fakes import ok as _ok

BC03_SINGLE = "62BC03FDEE3C730A000000AAAA"
BC06_SINGLE = "62BC06B480000000000000AAAA"
MULTI = "62BC03FDEE3C730A000000BC06B480000000000000AAAAAAAAAAAA"


class TestSplitHelpers:
    def test_did_data_len_strips_padding(self):
        assert _did_data_len(BC03_SINGLE, "BC03") == 8
        assert _did_data_len(BC06_SINGLE, "BC06") == 8

    def test_did_data_len_rejects_wrong_did(self):
        assert _did_data_len(BC03_SINGLE, "BC06") is None

    def test_split_real_multi(self):
        out = split_multi_did(MULTI, [("BC03", 8), ("BC06", 8)])
        assert out == {
            "BC03": "62BC03FDEE3C730A000000",
            "BC06": "62BC06B480000000000000",
        }

    def test_split_bad_order_fails(self):
        assert split_multi_did(MULTI, [("BC06", 8), ("BC03", 8)]) is None

    def test_split_wrong_length_fails(self):
        assert split_multi_did(MULTI, [("BC03", 7), ("BC06", 8)]) is None

    def test_split_non_62_fails(self):
        assert split_multi_did("7F2213", [("BC03", 8)]) is None

    def test_split_trailing_non_padding_fails(self):
        # Extra non-AA byte after the last DID's data → not a clean split.
        assert split_multi_did("62BC03FDEE3C730A00000099", [("BC03", 8)]) is None

    def test_custom_pad_byte(self):
        # A make that pads with 0x00 (not 0xAA): the pad byte is profile-driven.
        # (Data ends in a non-pad byte so stripping padding can't eat real data.)
        single = "62BC03FDEE3C730A0000FF0000"  # 8 data bytes + 00 pad
        assert _did_data_len(single, "BC03", pad=0x00) == 8
        multi = "62BC03FDEE3C730A0000FFBC06B4800000000000FF000000"
        assert split_multi_did(multi, [("BC03", 8), ("BC06", 8)], pad=0x00) == {
            "BC03": "62BC03FDEE3C730A0000FF",
            "BC06": "62BC06B4800000000000FF",
        }
        # With the default 0xAA pad the trailing 00s aren't padding → no split.
        assert split_multi_did(multi, [("BC03", 8), ("BC06", 8)]) is None

    def test_batchstate_carries_pad(self):
        bs = BatchState(pad=0x00)
        bs.learn(0x770, "BC03", "62BC03FDEE3C730A0000FF0000")
        assert bs.lengths[(0x770, "BC03")] == 8


class TestResolveMultiDidMax:
    def test_default_when_absent(self):
        assert resolve_multi_did_max(None) == 3
        assert resolve_multi_did_max({}) == 3

    def test_profile_level(self):
        assert resolve_multi_did_max({"multi_did_max": 6}) == 6

    def test_per_ecu_overrides_profile(self):
        assert resolve_multi_did_max({"multi_did_max": 6}, {"multi_did_max": 2}) == 2

    def test_invalid_falls_back_to_default(self):
        assert resolve_multi_did_max({"multi_did_max": 0}) == 3
        assert resolve_multi_did_max({"multi_did_max": -1}) == 3
        assert resolve_multi_did_max({"multi_did_max": "x"}) == 3


class TestTransportDidCap:
    """How many 2-byte DIDs fit in one `22 …` request, per transport."""

    def test_an_elm327_fits_three(self):
        # 7 data bytes: `22` + 3 x 2. This is why MULTI_DID_MAX_DEFAULT is 3.
        assert transport_did_cap(7) == 3

    def test_a_segmenting_transport_is_effectively_unbounded(self):
        assert transport_did_cap(ISOTP_MAX_REQUEST_BYTES) > 100

    def test_never_returns_a_useless_cap(self):
        # A cap of 0 would batch nothing *and* loop forever building groups.
        for n in (0, 1, 2, 3):
            assert transport_did_cap(n) == 1


class TestLoudDemotion:
    """Disabling batching is irreversible, so it must never be silent."""

    def test_disable_logs_once_with_the_reason(self, monkeypatch):
        events: list[tuple] = []
        monkeypatch.setattr(
            "canlib.modes.multi_batch.log_event",
            lambda cat, detail="", **kw: events.append((cat, detail, kw)),
        )
        bs = BatchState()
        bs.disable(0x770, "rejected a 3-DID request with NRC 0x13/0x31")
        bs.disable(0x770, "some other reason")

        assert 0x770 in bs.disabled
        assert len(events) == 1
        assert "0x770" in events[0][1]
        assert "NRC 0x13" in events[0][1]

    def test_note_clamp_is_true_only_the_first_time(self):
        bs = BatchState()
        assert bs.note_clamp(0x770, 6) is True
        assert bs.note_clamp(0x770, 6) is False
        assert bs.note_clamp(0x7E4, 6) is True  # per-ECU, not global


def _mk_sm(send_uds, max_request_bytes: int = ISOTP_MAX_REQUEST_BYTES):
    sm = MagicMock()
    sm.keepalive_stale = AsyncMock()
    sm.has_session = MagicMock(return_value=True)
    sm.terminal = MagicMock()
    sm.terminal.set_header = AsyncMock()
    sm.terminal.send_uds = send_uds
    sm.terminal.max_request_bytes = max_request_bytes
    sm.terminal.diag.transport = "fake"
    return sm


def _igpm_index(multi_did: bool) -> dict:
    ecu = {"tx_id": 0x770, "pids": {"22BC03": {"parameters": {}}, "22BC06": {"parameters": {}}}}
    if multi_did:
        ecu["multi_did"] = True
    return {"IGPM": ecu}


BC04_SINGLE = "62BC04B53FF4EA01000042AAAA"
MULTI3 = "62BC03FDEE3C730A000000BC04B53FF4EA01000042BC06B480000000000000AAAA"


def _igpm_index3(multi_did: bool = True, max_dids: int | None = None) -> dict:
    ecu = {
        "tx_id": 0x770,
        "pids": {
            "22BC03": {"parameters": {}},
            "22BC04": {"parameters": {}},
            "22BC06": {"parameters": {}},
        },
        "multi_did": multi_did,
    }
    if max_dids is not None:
        ecu["multi_did_max"] = max_dids
    return {"IGPM": ecu}


_SINGLES = {
    "22BC03": _ok(BC03_SINGLE),
    "22BC06": _ok(BC06_SINGLE),
}


class TestBatchingExecutor:
    def test_learns_then_batches(self):
        term = FakeTerminal({**_SINGLES, "22BC03BC06": _ok(MULTI)})
        sm = _mk_sm(term.send_uds)
        bs = BatchState()
        idx = _igpm_index(multi_did=True)

        # Cycle 1: no known lengths yet → single reads that learn lengths.
        _l, r1 = asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert term.sent == ["22BC03", "22BC06"]
        assert bs.lengths[(0x770, "BC03")] == 8
        assert bs.lengths[(0x770, "BC06")] == 8
        assert len(r1) == 2

        # Cycle 2: lengths known → one batched request replaces two singles.
        term.sent.clear()
        _l, r2 = asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert term.sent == ["22BC03BC06"]
        got = {x["pid"]: x["raw_hex"] for x in r2}
        assert got["22BC03"] == "62BC03FDEE3C730A000000"
        assert got["22BC06"] == "62BC06B480000000000000"

    def test_batches_up_to_cap(self):
        # multi_did_max=3 (default) → all three consecutive DIDs in one request.
        term = FakeTerminal({**_SINGLES, "22BC04": _ok(BC04_SINGLE), "22BC03BC04BC06": _ok(MULTI3)})
        sm = _mk_sm(term.send_uds)
        bs = BatchState()
        for d in ("BC03", "BC04", "BC06"):
            bs.lengths[(0x770, d)] = 8
        idx = _igpm_index3()

        _l, r = asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert term.sent == ["22BC03BC04BC06"]  # one request for all three
        assert {x["pid"] for x in r} == {"22BC03", "22BC04", "22BC06"}

    def test_per_ecu_cap_splits_into_two_requests(self):
        # multi_did_max=2 → a run of 3 splits 2+1: BC03+BC04 batched, BC06 single.
        term = FakeTerminal(
            {
                **_SINGLES,
                "22BC04": _ok(BC04_SINGLE),
                "22BC03BC04": _ok("62BC03FDEE3C730A000000BC04B53FF4EA01000042AAAA"),
            }
        )
        sm = _mk_sm(term.send_uds)
        bs = BatchState()
        for d in ("BC03", "BC04", "BC06"):
            bs.lengths[(0x770, d)] = 8
        idx = _igpm_index3(max_dids=2)

        _l, r = asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert term.sent == ["22BC03BC04", "22BC06"]
        assert {x["pid"] for x in r} == {"22BC03", "22BC04", "22BC06"}

    def test_a_profile_cap_is_clamped_to_what_the_transport_can_send(self):
        """The Ioniq ships `multi_did_max: 6`, which no ELM327 can put on the wire.

        An over-long request comes back as a bare `?` — no NRC, so the caller reads
        it as "this ECU can't batch" and demotes it to per-DID reads for the whole
        session. Clamping up front keeps the profile portable: the same
        `multi_did_max` is honoured on a segmenting transport and trimmed here.
        """
        term = FakeTerminal({**_SINGLES, "22BC04": _ok(BC04_SINGLE), "22BC03BC04BC06": _ok(MULTI3)})
        sm = _mk_sm(term.send_uds, max_request_bytes=7)  # an ELM327
        bs = BatchState()
        for d in ("BC03", "BC04", "BC06"):
            bs.lengths[(0x770, d)] = 8
        idx = _igpm_index3(max_dids=6)

        asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        # 7 data bytes = `22` + three DIDs, so the 6 asked for became 3.
        assert term.sent == ["22BC03BC04BC06"]

    def test_a_cap_within_the_transport_ceiling_is_untouched(self):
        term = FakeTerminal({**_SINGLES, "22BC03BC06": _ok(MULTI)})
        sm = _mk_sm(term.send_uds, max_request_bytes=7)
        bs = BatchState()
        for d in ("BC03", "BC06"):
            bs.lengths[(0x770, d)] = 8

        asyncio.run(
            _exec_query(
                sm, "IGPM", [], _igpm_index(True), {}, False, return_results=True, batch_state=bs
            )
        )
        assert term.sent == ["22BC03BC06"]

    def test_batched_request_is_echo_validated(self):
        """A batch must carry its own echo expectation, not just a SID.

        The batch path used to pass no `expected_sid` at all, so a response that
        had slipped one slot behind still parsed as a valid `0x62` reply and its
        bytes were split into the *wrong* DIDs — silently wrong decoded values,
        with nothing logged. Validating the leading DID makes the desync a
        detectable stale response that triggers a pipe resync instead.
        """
        term = FakeTerminal({**_SINGLES, "22BC03BC06": _ok(MULTI)})
        sm = _mk_sm(term.send_uds)
        bs = BatchState()
        for d in ("BC03", "BC06"):
            bs.lengths[(0x770, d)] = 8

        asyncio.run(
            _exec_query(
                sm,
                "IGPM",
                [],
                _igpm_index(multi_did=True),
                {},
                False,
                return_results=True,
                batch_state=bs,
            )
        )
        assert term.sent == ["22BC03BC06"]
        kwargs = term.uds_kwargs[-1]
        assert kwargs["expected_sid"] == 0x22
        assert kwargs["expected_echo"] == b"\xbc\x03"

    def test_batch_holds_the_transaction_across_header_and_request(self):
        # The header is per-ECU adapter state; a keepalive that retargets it
        # between ATSH and the batch request sends the DIDs to the wrong ECU.
        term = FakeTerminal({**_SINGLES, "22BC03BC06": _ok(MULTI)})
        sm = _mk_sm(term.send_uds)
        sm.terminal = term
        bs = BatchState()
        for d in ("BC03", "BC06"):
            bs.lengths[(0x770, d)] = 8

        asyncio.run(
            _exec_query(
                sm,
                "IGPM",
                [],
                _igpm_index(multi_did=True),
                {},
                False,
                return_results=True,
                batch_state=bs,
            )
        )
        names = [c[0] for c in term.calls]
        assert names[-4:] == ["transaction_enter", "set_header", "send_uds", "transaction_exit"]

    def test_nrc13_disables_and_falls_back(self):
        term = FakeTerminal(
            {**_SINGLES, "22BC03BC06": _nrc(0x13, "incorrectMessageLengthOrInvalidFormat")}
        )
        sm = _mk_sm(term.send_uds)
        bs = BatchState()
        # Pre-seed lengths so a batch is attempted immediately.
        bs.lengths[(0x770, "BC03")] = 8
        bs.lengths[(0x770, "BC06")] = 8
        idx = _igpm_index(multi_did=True)

        _l, r = asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert term.sent[0] == "22BC03BC06"  # batch attempted first
        assert 0x770 in bs.disabled  # then disabled
        assert set(term.sent[1:]) == {"22BC03", "22BC06"}  # fell back to per-DID
        assert len(r) == 2

        # Next cycle: batching stays disabled → straight to per-DID.
        term.sent.clear()
        asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert "22BC03BC06" not in term.sent
        assert set(term.sent) == {"22BC03", "22BC06"}

    def test_flag_off_never_batches(self):
        term = FakeTerminal(dict(_SINGLES))
        sm = _mk_sm(term.send_uds)
        bs = BatchState()
        bs.lengths[(0x770, "BC03")] = 8
        bs.lengths[(0x770, "BC06")] = 8
        idx = _igpm_index(multi_did=False)  # ECU not opted in

        asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert term.sent == ["22BC03", "22BC06"]  # singles only

    def test_no_batch_state_is_single(self):
        term = FakeTerminal(dict(_SINGLES))
        sm = _mk_sm(term.send_uds)
        idx = _igpm_index(multi_did=True)
        # No batch_state passed → single reads (the _exec_query API contract; the
        # one-shot pipeline now supplies a shared BatchState, tested via the
        # learn→batch case above).
        asyncio.run(_exec_query(sm, "IGPM", [], idx, {}, False, return_results=True))
        assert term.sent == ["22BC03", "22BC06"]


class TestReadSingleEchoValidation:
    """_read_single derives + passes the response echo so a mislabeled/stale
    frame (e.g. a 6101 response to a 2102 request) is rejected, not stored."""

    def test_service_21_passes_pid_echo(self):
        term = FakeTerminal({"2102": _ok("6102F8F8")})
        sm = _mk_sm(term.send_uds)
        asyncio.run(_read_single(sm, 0x7E2, "2102", {"parameters": {}}, [], None))
        assert term.uds_kwargs[0]["expected_sid"] == 0x21
        assert term.uds_kwargs[0]["expected_echo"] == b"\x02"

    def test_service_22_passes_did_echo(self):
        term = FakeTerminal({"22BC03": _ok("62BC0300")})
        sm = _mk_sm(term.send_uds)
        asyncio.run(_read_single(sm, 0x770, "22BC03", {"parameters": {}}, [], None))
        assert term.uds_kwargs[0]["expected_sid"] == 0x22
        assert term.uds_kwargs[0]["expected_echo"] == b"\xbc\x03"

    def test_mismatched_frame_becomes_error_not_stored(self):
        # Response depends on the passed echo/SID (the parser rejecting a 6101
        # response to a 2102 request), so this stays a local computed closure.
        async def send_uds(req, *a, **k):
            from canlib.uds.parse import parse_uds_response

            return parse_uds_response(
                "6101FFE0",
                expected_sid=k.get("expected_sid"),
                expected_echo=k.get("expected_echo"),
            )

        sm = _mk_sm(send_uds)
        result = asyncio.run(_read_single(sm, 0x7E2, "2102", {"parameters": {}}, [], None))
        assert "error" in result
        assert "raw_hex" not in result  # not recorded as a valid payload


class TestLeakedBatchReply:
    """A batch that timed out can answer the per-DID fallback read of its first DID.

    The batch reply starts ``62 BC03``, so the single read's echo check passes it. Taken, it
    was saved as BC03's payload with BC06's data appended (seen in real 2026-08-08 captures),
    and learned, it made BC03's length the whole batch's, so every later batch failed to split.
    """

    def _state(self) -> BatchState:
        bs = BatchState()
        for d in ("BC03", "BC06"):
            bs.lengths[(0x770, d)] = 8
        return bs

    def test_single_read_answered_by_the_batch_is_an_error_not_a_payload(self):
        term = FakeTerminal({"22BC03": _ok(MULTI)})
        bs = self._state()
        sm = _mk_sm(term.send_uds)
        result = asyncio.run(_read_single(sm, 0x770, "22BC03", {"parameters": {}}, False, bs))
        assert "raw_hex" not in result
        assert "multi-DID batch reply" in result["error"]
        assert bs.lengths[(0x770, "BC03")] == 8  # not re-learned as the batch's length

    def test_timed_out_batch_whose_reply_lands_in_the_fallback_is_not_stored(self):
        term = FakeTerminal({"22BC03": _ok(MULTI), "22BC06": _ok(BC06_SINGLE)})  # batch: NO DATA
        sm = _mk_sm(term.send_uds)
        _l, results = asyncio.run(
            _exec_query(
                sm,
                "IGPM",
                [],
                _igpm_index(multi_did=True),
                {},
                False,
                return_results=True,
                batch_state=self._state(),
            )
        )
        assert term.sent == ["22BC03BC06", "22BC03", "22BC06"]
        by_pid = {r["pid"]: r for r in results}
        assert "raw_hex" not in by_pid["22BC03"]
        assert by_pid["22BC06"]["raw_hex"] == BC06_SINGLE

    def test_without_learned_lengths_a_single_reply_is_taken_as_is(self):
        term = FakeTerminal({"22BC03": _ok(BC03_SINGLE)})
        sm = _mk_sm(term.send_uds)
        result = asyncio.run(
            _read_single(sm, 0x770, "22BC03", {"parameters": {}}, False, BatchState())
        )
        assert result["raw_hex"] == BC03_SINGLE

    def test_a_leak_stops_batching_so_the_next_cycle_reads_both_dids(self, monkeypatch):
        """Two cycles: the leak costs one reading once, not D1 and D2 every cycle."""
        events: list[tuple] = []
        monkeypatch.setattr(
            "canlib.modes.multi_exec.log_event",
            lambda cat, detail="", **kw: events.append((cat, detail, kw)),
        )
        replies = {"22BC03": [_ok(MULTI), _ok(BC03_SINGLE)], "22BC06": [_ok(BC06_SINGLE)] * 2}

        async def send_uds(req, *a, **k):
            sent.append(req)
            queue = replies.get(req)
            return dict(queue.pop(0)) if queue else {"ok": False, "error": "NO DATA"}

        sent: list[str] = []
        sm = _mk_sm(send_uds)
        bs = self._state()
        idx = _igpm_index(multi_did=True)
        run = lambda: asyncio.run(  # noqa: E731
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )

        run()
        assert sent == ["22BC03BC06", "22BC03", "22BC06"]
        assert 0x770 in bs.disabled
        assert ("stale", LEAKED_BATCH_ERROR) in [(c, d) for c, d, _kw in events]

        sent.clear()
        _l, second = run()
        assert sent == ["22BC03", "22BC06"]  # no batch to time out and leak again
        assert {r["pid"]: r["raw_hex"] for r in second} == {
            "22BC03": BC03_SINGLE,
            "22BC06": BC06_SINGLE,
        }

    def test_the_leak_is_logged_as_a_stale_event(self, monkeypatch):
        events: list[tuple] = []
        monkeypatch.setattr(
            "canlib.modes.multi_exec.log_event",
            lambda cat, detail="", **kw: events.append((cat, detail, kw)),
        )
        sm = _mk_sm(FakeTerminal({"22BC03": _ok(MULTI)}).send_uds)
        asyncio.run(_read_single(sm, 0x770, "22BC03", {"parameters": {}}, False, self._state()))
        assert events == [
            ("stale", LEAKED_BATCH_ERROR, {"level": logging.INFO, "ecu": "0x770", "pid": "22BC03"})
        ]

    def test_non_hex_is_never_a_leaked_batch(self):
        assert is_leaked_batch_reply("62BC03ZZ", {"BC03": 8}.get) is False

    def test_a_lower_case_pid_key_still_batches_and_still_catches_a_leak(self):
        idx = {
            "IGPM": {
                "tx_id": 0x770,
                "multi_did": True,
                "pids": {"22bc03": {"parameters": {}}, "22bc06": {"parameters": {}}},
            }
        }
        term = FakeTerminal({"22bc03": _ok(BC03_SINGLE), "22bc06": _ok(BC06_SINGLE)})
        sm = _mk_sm(term.send_uds)
        bs = BatchState()
        for _ in range(2):
            asyncio.run(
                _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
            )
        assert bs.lengths == {(0x770, "BC03"): 8, (0x770, "BC06"): 8}
        assert term.sent[2] == "22BC03BC06"  # learned under the upper-case DID, then batched

        term = FakeTerminal({"22bc03": _ok(MULTI)})
        result = asyncio.run(
            _read_single(_mk_sm(term.send_uds), 0x770, "22bc03", {"parameters": {}}, False, bs)
        )
        assert result["error"] == LEAKED_BATCH_ERROR


class TestUnansweredBatches:
    """A batch that keeps timing out while its DIDs answer singly stops being sent."""

    def _run(self, bs, term, cycles):
        sm = _mk_sm(term.send_uds)
        idx = _igpm_index(multi_did=True)
        for _ in range(cycles):
            asyncio.run(
                _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
            )

    def _state(self) -> BatchState:
        bs = BatchState()
        for d in ("BC03", "BC06"):
            bs.lengths[(0x770, d)] = 8
        return bs

    def test_disabled_after_a_run_of_unanswered_batches(self):
        bs = self._state()
        term = FakeTerminal(_SINGLES)  # the batch gets NO DATA
        self._run(bs, term, BATCH_UNANSWERED_BEFORE_DISABLE - 1)
        assert 0x770 not in bs.disabled
        self._run(bs, term, 1)
        assert 0x770 in bs.disabled
        term.sent.clear()
        self._run(bs, term, 1)
        assert term.sent == ["22BC03", "22BC06"]

    def test_an_ecu_that_answers_nothing_keeps_batching(self):
        # Asleep, not slow: the singles fail too, so the batch is not to blame.
        bs = self._state()
        self._run(bs, FakeTerminal(), BATCH_UNANSWERED_BEFORE_DISABLE + 1)
        assert 0x770 not in bs.disabled

    def test_an_answered_batch_resets_the_run(self):
        bs = self._state()
        self._run(bs, FakeTerminal(_SINGLES), BATCH_UNANSWERED_BEFORE_DISABLE - 1)
        self._run(bs, FakeTerminal({**_SINGLES, "22BC03BC06": _ok(MULTI)}), 1)
        self._run(bs, FakeTerminal(_SINGLES), BATCH_UNANSWERED_BEFORE_DISABLE - 1)
        assert 0x770 not in bs.disabled

    def test_an_nrc_is_not_unanswered(self):
        bs = self._state()
        term = FakeTerminal({**_SINGLES, "22BC03BC06": _nrc(0x22, "conditionsNotCorrect")})
        self._run(bs, term, BATCH_UNANSWERED_BEFORE_DISABLE + 1)
        assert 0x770 not in bs.disabled


class TestLearnedLengthPadding:
    """Only a response that fills its last CAN frame can carry padding, and only in that frame."""

    def test_a_response_with_room_in_its_last_frame_is_taken_at_its_length(self):
        # 11 bytes: the second frame has room to spare, so its trailing AA is data.
        assert _did_data_len("62BC03FDEE3C730A00AAAA", "BC03") == 8
        assert _did_data_len("62BC030102AA", "BC03") == 3  # single frame, 6 of 7 bytes

    def test_padding_is_stripped_only_within_the_final_frame(self):
        # 20 bytes fill three frames; the third holds at least one real byte, so 14 remain.
        resp = "62BC03" + "11" * 10 + "AA" * 7
        assert _did_data_len(resp, "BC03") == 11

    def test_a_did_is_never_learned_with_no_data(self):
        assert _did_data_len("62BC03AAAAAAAA", "BC03") == 1
        bs = BatchState()
        bs.learn(0x770, "BC03", "62BC03")
        assert bs.lengths == {}
