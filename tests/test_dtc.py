"""Tests for canlib.modes.dtc — DTC decoding + read/clear mode orchestration."""

import argparse
import json

import pytest

from canlib.modes import dtc


class FakeTerminal:
    """Minimal terminal returning canned send_uds responses by request string."""

    def __init__(self, responses):
        self._responses = responses
        self.sent = []

    async def set_header(self, tx_id):
        pass

    async def enter_extended_session(self, wake=False):
        return True, None

    async def send_uds(self, cmd, timeout=None, expected_sid=None, expected_did=None):
        self.sent.append(cmd)
        resp = self._responses.get(cmd, {"ok": False, "error": "NO DATA", "raw": "NO DATA"})
        return dict(resp)


def _ok(hex_str):
    b = bytes.fromhex(hex_str)
    return {"ok": True, "bytes": b, "hex": hex_str.upper(), "raw": hex_str}


class TestFormatDtc:
    def test_powertrain(self):
        # 0x01 0x23 0x00 -> P0123-00
        assert dtc.format_dtc(0x01, 0x23, 0x00) == "P0123-00"

    def test_network_category(self):
        # top two bits 11 -> U
        assert dtc.format_dtc(0xC1, 0x07, 0x04) == "U0107-04"

    def test_body_and_chassis(self):
        assert dtc.format_dtc(0x80, 0x00, 0x00)[0] == "B"
        assert dtc.format_dtc(0x40, 0x00, 0x00)[0] == "C"

    def test_second_digit_hex(self):
        # low nibble of b0 becomes hex second digit
        assert dtc.format_dtc(0x0A, 0x00, 0x00) == "P0A00-00"


class TestDecodeStatus:
    def test_single_bit(self):
        assert dtc.decode_status(0x01) == ["testFailed"]
        assert dtc.decode_status(0x08) == ["confirmedDTC"]

    def test_multiple_bits(self):
        assert dtc.decode_status(0x09) == ["testFailed", "confirmedDTC"]

    def test_none(self):
        assert dtc.decode_status(0x00) == []


class TestDecodeRecords:
    def test_two_records(self):
        recs = dtc.decode_dtc_records(bytes.fromhex("0123002F" + "C1070408"))
        assert len(recs) == 2
        assert recs[0]["dtc"] == "P0123-00"
        assert recs[0]["status"] == 0x2F
        assert recs[1]["dtc"] == "U0107-04"
        assert recs[1]["status"] == 0x08

    def test_ignores_trailing_partial(self):
        recs = dtc.decode_dtc_records(bytes.fromhex("0123002F" + "AABB"))
        assert len(recs) == 1

    def test_empty(self):
        assert dtc.decode_dtc_records(b"") == []


class TestModeRead:
    @pytest.mark.asyncio
    async def test_read_json(self, capsys):
        # 59 02 <availMask=FF> <DTC P0123-00 status 2F>
        t = FakeTerminal({"1902FF": _ok("5902FF" + "0123002F")})
        await dtc.mode_dtc_read(t, 0x7E4, mask=0xFF, as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["count"] == 1
        assert data["dtcs"][0]["dtc"] == "P0123-00"
        assert data["dtcs"][0]["status"] == "0x2F"
        assert "confirmedDTC" in data["dtcs"][0]["status_bits"]
        assert t.sent == ["1902FF"]

    @pytest.mark.asyncio
    async def test_read_no_dtcs(self, capsys):
        t = FakeTerminal({"1902FF": _ok("5902FF")})
        await dtc.mode_dtc_read(t, 0x7E4, mask=0xFF, as_json=False)
        assert "No DTCs stored" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_read_custom_mask(self, capsys):
        t = FakeTerminal({"190208": _ok("590208")})
        await dtc.mode_dtc_read(t, 0x7E4, mask=0x08, as_json=True)
        assert t.sent == ["190208"]

    @pytest.mark.asyncio
    async def test_read_nrc(self, capsys):
        t = FakeTerminal(
            {"1902FF": {"ok": False, "nrc": 0x11, "nrc_desc": "serviceNotSupported"}}
        )
        await dtc.mode_dtc_read(t, 0x7E4, as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["nrc"] == "0x11"


class TestModeClear:
    @pytest.mark.asyncio
    async def test_clear_ok(self, capsys):
        t = FakeTerminal({"14FFFFFF": _ok("54")})
        await dtc.mode_dtc_clear(t, 0x7E4, group=0xFFFFFF, as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["cleared"] is True
        assert data["group"] == "0xFFFFFF"
        assert t.sent == ["14FFFFFF"]

    @pytest.mark.asyncio
    async def test_clear_nrc(self, capsys):
        t = FakeTerminal(
            {"14FFFFFF": {"ok": False, "nrc": 0x22, "nrc_desc": "conditionsNotCorrect"}}
        )
        await dtc.mode_dtc_clear(t, 0x7E4, as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["cleared"] is False
        assert data["nrc"] == "0x22"


class TestDispatchTransportAgnostic:
    """The dtc branch of dispatch_mode is shared by both transports.

    Both the WiCAN WebSocket (WiCANTerminal) and slcan-tcp (RawTerminal) run
    live commands through the same ``dispatch_mode``. Exercising it with a fake
    terminal proves the dtc command is transport-agnostic — the same code path
    the RawTerminal adapter drives over SLCAN.
    """

    def _args(self, **kw):
        from canlib.commands._live import CANREQ_DEFAULTS

        return argparse.Namespace(**{**CANREQ_DEFAULTS, **kw})

    @pytest.mark.asyncio
    async def test_dispatch_read(self, capsys):
        from canlib.commands._live import dispatch_mode

        t = FakeTerminal({"1902FF": _ok("5902FF" + "0123002F")})
        args = self._args(dtc="7E4", mask="FF", clear=False)
        await dispatch_mode(args, t, {}, "1.2.3.4")
        assert t.sent == ["1902FF"]
        assert "P0123-00" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_dispatch_clear_with_yes(self, capsys):
        from canlib.commands._live import dispatch_mode

        t = FakeTerminal({"14FFFFFF": _ok("54")})
        args = self._args(dtc="7E4", clear=True, group="FFFFFF", yes=True)
        await dispatch_mode(args, t, {}, "1.2.3.4")
        assert t.sent == ["14FFFFFF"]
        assert "cleared" in capsys.readouterr().out.lower()

