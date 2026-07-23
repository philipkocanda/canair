"""Tests for UDS service-22 multi-DID batching (canlib.modes.multi).

Uses the real IGPM (0x770) responses captured on-device:
    22BC03      -> 62BC03 FDEE3C730A000000 (padded AAAA)
    22BC06      -> 62BC06 B480000000000000 (padded AAAA)
    22BC03BC06  -> 62BC03 FDEE3C730A000000 BC06 B480000000000000 (padded AA…)
BCM rejects multi-DID with 7F2213 (NRC 0x13).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from canlib.modes.multi import (
    BatchState,
    _did_data_len,
    _exec_query,
    _read_single,
    split_multi_did,
)

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


def _mk_sm(send_uds):
    sm = MagicMock()
    sm.keepalive_stale = AsyncMock()
    sm.has_session = MagicMock(return_value=True)
    sm.terminal = MagicMock()
    sm.terminal.set_header = AsyncMock()
    sm.terminal.send_uds = send_uds
    return sm


def _igpm_index(multi_did: bool) -> dict:
    ecu = {"tx_id": 0x770, "pids": {"22BC03": {"parameters": {}}, "22BC06": {"parameters": {}}}}
    if multi_did:
        ecu["multi_did"] = True
    return {"IGPM": ecu}


_SINGLES = {
    "22BC03": {"ok": True, "hex": BC03_SINGLE, "bytes": bytes.fromhex(BC03_SINGLE)},
    "22BC06": {"ok": True, "hex": BC06_SINGLE, "bytes": bytes.fromhex(BC06_SINGLE)},
}


class TestBatchingExecutor:
    def test_learns_then_batches(self):
        calls = []

        async def send_uds(req, *a, **k):
            calls.append(req)
            if req == "22BC03BC06":
                return {"ok": True, "hex": MULTI, "bytes": bytes.fromhex(MULTI)}
            return _SINGLES[req]

        sm = _mk_sm(send_uds)
        bs = BatchState()
        idx = _igpm_index(multi_did=True)

        # Cycle 1: no known lengths yet → single reads that learn lengths.
        _l, r1 = asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert calls == ["22BC03", "22BC06"]
        assert bs.lengths[(0x770, "BC03")] == 8
        assert bs.lengths[(0x770, "BC06")] == 8
        assert len(r1) == 2

        # Cycle 2: lengths known → one batched request replaces two singles.
        calls.clear()
        _l, r2 = asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert calls == ["22BC03BC06"]
        got = {x["pid"]: x["raw_hex"] for x in r2}
        assert got["22BC03"] == "62BC03FDEE3C730A000000"
        assert got["22BC06"] == "62BC06B480000000000000"

    def test_nrc13_disables_and_falls_back(self):
        calls = []

        async def send_uds(req, *a, **k):
            calls.append(req)
            if req == "22BC03BC06":
                return {
                    "ok": False,
                    "nrc": 0x13,
                    "nrc_desc": "incorrectMessageLengthOrInvalidFormat",
                }
            return _SINGLES[req]

        sm = _mk_sm(send_uds)
        bs = BatchState()
        # Pre-seed lengths so a batch is attempted immediately.
        bs.lengths[(0x770, "BC03")] = 8
        bs.lengths[(0x770, "BC06")] = 8
        idx = _igpm_index(multi_did=True)

        _l, r = asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert calls[0] == "22BC03BC06"  # batch attempted first
        assert 0x770 in bs.disabled  # then disabled
        assert set(calls[1:]) == {"22BC03", "22BC06"}  # fell back to per-DID
        assert len(r) == 2

        # Next cycle: batching stays disabled → straight to per-DID.
        calls.clear()
        asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert "22BC03BC06" not in calls
        assert set(calls) == {"22BC03", "22BC06"}

    def test_flag_off_never_batches(self):
        calls = []

        async def send_uds(req, *a, **k):
            calls.append(req)
            return _SINGLES[req]

        sm = _mk_sm(send_uds)
        bs = BatchState()
        bs.lengths[(0x770, "BC03")] = 8
        bs.lengths[(0x770, "BC06")] = 8
        idx = _igpm_index(multi_did=False)  # ECU not opted in

        asyncio.run(
            _exec_query(sm, "IGPM", [], idx, {}, False, return_results=True, batch_state=bs)
        )
        assert calls == ["22BC03", "22BC06"]  # singles only

    def test_no_batch_state_is_single(self):
        calls = []

        async def send_uds(req, *a, **k):
            calls.append(req)
            return _SINGLES[req]

        sm = _mk_sm(send_uds)
        idx = _igpm_index(multi_did=True)
        # No batch_state passed → single reads (the _exec_query API contract; the
        # one-shot pipeline now supplies a shared BatchState, tested via the
        # learn→batch case above).
        asyncio.run(_exec_query(sm, "IGPM", [], idx, {}, False, return_results=True))
        assert calls == ["22BC03", "22BC06"]


class TestReadSingleEchoValidation:
    """_read_single derives + passes the response echo so a mislabeled/stale
    frame (e.g. a 6101 response to a 2102 request) is rejected, not stored."""

    def test_service_21_passes_pid_echo(self):
        seen = {}

        async def send_uds(req, *a, **k):
            seen.update(k)
            return {"ok": True, "hex": "6102F8F8", "bytes": bytes.fromhex("6102F8F8")}

        sm = _mk_sm(send_uds)
        asyncio.run(_read_single(sm, 0x7E2, "2102", {"parameters": {}}, [], None))
        assert seen["expected_sid"] == 0x21
        assert seen["expected_echo"] == b"\x02"

    def test_service_22_passes_did_echo(self):
        seen = {}

        async def send_uds(req, *a, **k):
            seen.update(k)
            return {"ok": True, "hex": "62BC0300", "bytes": bytes.fromhex("62BC0300")}

        sm = _mk_sm(send_uds)
        asyncio.run(_read_single(sm, 0x770, "22BC03", {"parameters": {}}, [], None))
        assert seen["expected_sid"] == 0x22
        assert seen["expected_echo"] == b"\xbc\x03"

    def test_mismatched_frame_becomes_error_not_stored(self):
        # Simulate the parser rejecting a 6101 response to a 2102 request.
        async def send_uds(req, *a, **k):
            from canlib.uds_parse import parse_uds_response

            return parse_uds_response(
                "6101FFE0",
                expected_sid=k.get("expected_sid"),
                expected_echo=k.get("expected_echo"),
            )

        sm = _mk_sm(send_uds)
        result = asyncio.run(_read_single(sm, 0x7E2, "2102", {"parameters": {}}, [], None))
        assert "error" in result
        assert "raw_hex" not in result  # not recorded as a valid payload
