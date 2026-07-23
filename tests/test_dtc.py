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

    async def send_uds(
        self, cmd, timeout=None, expected_sid=None, expected_did=None, expected_echo=None
    ):
        self.sent.append(cmd)
        resp = self._responses.get(cmd, {"ok": False, "error": "NO DATA", "raw": "NO DATA"})
        return dict(resp)


def _ok(hex_str):
    b = bytes.fromhex(hex_str)
    return {"ok": True, "bytes": b, "hex": hex_str.upper(), "raw": hex_str}


class FlakyTerminal:
    """Returns NO DATA the first time a request is seen, then the canned payload
    — models a slow/asleep ECU that answers only on the wake+longer-timeout retry."""

    def __init__(self, recover):
        self._recover = recover  # request -> hex payload (returned after first miss)
        self._seen: set[str] = set()
        self.sent: list[str] = []

    async def set_header(self, tx_id):
        pass

    async def enter_extended_session(self, wake=False):
        return True, None

    async def send_uds(
        self, cmd, timeout=None, expected_sid=None, expected_did=None, expected_echo=None
    ):
        self.sent.append(cmd)
        if cmd in self._recover and cmd in self._seen:
            return _ok(self._recover[cmd])
        self._seen.add(cmd)
        return {"ok": False, "error": "NO DATA", "raw": "NO DATA"}


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
        await dtc.mode_dtc_read(t, 0x7E4, mask=0xFF, protocol="uds", as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["protocol"] == "uds"
        assert data["count"] == 1
        assert data["dtcs"][0]["dtc"] == "P0123-00"
        assert data["dtcs"][0]["status"] == "0x2F"
        assert "confirmedDTC" in data["dtcs"][0]["status_bits"]
        assert data["dtcs"][0]["interpretation"]["category"] == "Powertrain"
        assert t.sent == ["1902FF"]

    @pytest.mark.asyncio
    async def test_read_no_dtcs(self, capsys):
        t = FakeTerminal({"1902FF": _ok("5902FF")})
        await dtc.mode_dtc_read(t, 0x7E4, mask=0xFF, protocol="uds", as_json=False)
        assert "No DTCs stored" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_read_custom_mask(self, capsys):
        t = FakeTerminal({"190208": _ok("590208")})
        await dtc.mode_dtc_read(t, 0x7E4, mask=0x08, protocol="uds", as_json=True)
        assert t.sent == ["190208"]

    @pytest.mark.asyncio
    async def test_read_nrc(self, capsys):
        t = FakeTerminal({"1902FF": {"ok": False, "nrc": 0x11, "nrc_desc": "serviceNotSupported"}})
        await dtc.mode_dtc_read(t, 0x7E4, protocol="uds", as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["nrc"] == "0x11"

    @pytest.mark.asyncio
    async def test_mask_fallback_on_request_out_of_range(self, capsys):
        # ECU rejects FF with NRC 0x31, then accepts 0x08 (IGPM behavior).
        t = FakeTerminal(
            {
                "1902FF": {"ok": False, "nrc": 0x31, "nrc_desc": "requestOutOfRange"},
                "190208": _ok("590209"),
            }
        )
        await dtc.mode_dtc_read(t, 0x770, mask=0xFF, protocol="uds", as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert t.sent == ["1902FF", "190208"]
        assert data["command"] == "190208"
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_no_fallback_when_mask_already_08(self, capsys):
        t = FakeTerminal({"190208": {"ok": False, "nrc": 0x31, "nrc_desc": "requestOutOfRange"}})
        await dtc.mode_dtc_read(t, 0x770, mask=0x08, protocol="uds", as_json=True)
        # No infinite/duplicate retry — one request only.
        assert t.sent == ["190208"]


class TestModeReadKwp:
    @pytest.mark.asyncio
    async def test_kwp_read_decodes_2byte_dtcs(self, capsys):
        # 58 <count=1> <DTC P1234 status 20>
        t = FakeTerminal({"1800FF00": _ok("5801" + "1234" + "20")})
        await dtc.mode_dtc_read(t, 0x7E4, protocol="kwp", as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["protocol"] == "kwp"
        assert data["command"] == "1800FF00"
        assert data["count"] == 1
        assert data["dtcs"][0]["dtc"] == "P1234"
        assert data["dtcs"][0]["status"] == "0x20"

    @pytest.mark.asyncio
    async def test_kwp_read_probes_alternate_forms(self, capsys):
        # First form NRCs, second form succeeds.
        t = FakeTerminal(
            {
                "1800FF00": {"ok": False, "nrc": 0x12, "nrc_desc": "subFunctionNotSupported"},
                "1802FF00": _ok("5800"),
            }
        )
        await dtc.mode_dtc_read(t, 0x7E4, protocol="kwp", as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert t.sent == ["1800FF00", "1802FF00"]
        assert data["count"] == 0


class TestFormatKwpDtc:
    def test_two_byte_no_failure_type(self):
        assert dtc.format_kwp_dtc(0x01, 0x23) == "P0123"
        assert dtc.format_kwp_dtc(0xC1, 0x07) == "U0107"


class TestScanLog:
    @pytest.mark.asyncio
    async def test_read_logs_baseline_then_cleared(self, monkeypatch, tmp_path, capsys):
        from canlib import dtc_log

        logp = tmp_path / "dtc_log.yaml"
        monkeypatch.setattr(dtc_log, "log_path", lambda path=None: logp if path is None else path)

        # First scan: BMS has one DTC -> recorded as baseline.
        t1 = FakeTerminal({"1902FF": _ok("5902FF" + "0123002F")})
        await dtc.mode_dtc_read(t1, 0x7E4, protocol="uds", log=True)
        out1 = capsys.readouterr().out
        assert "baseline" in out1
        assert logp.exists()

        # Second scan: BMS now clean -> the code shows as cleared vs baseline.
        t2 = FakeTerminal({"1902FF": _ok("5902FF")})
        await dtc.mode_dtc_read(t2, 0x7E4, protocol="uds", log=True)
        out2 = capsys.readouterr().out
        assert "cleared" in out2
        assert "P0123-00" in out2
        # Two scans recorded, same scope.
        assert len(dtc_log.load_log(logp)["scans"]) == 2
        # The self-clear is also recorded as a first-class 'detected' clear event.
        clears = dtc_log.load_log(logp).get("clears", [])
        assert len(clears) == 1
        assert clears[0]["type"] == "detected"
        assert ["BMS (0x7E4)", "P0123-00"] in clears[0]["cleared"]

    @pytest.mark.asyncio
    async def test_manual_clear_logs_event_and_clean_baseline(self, monkeypatch, tmp_path, capsys):
        from canlib import dtc_log

        logp = tmp_path / "dtc_log.yaml"
        monkeypatch.setattr(dtc_log, "log_path", lambda path=None: logp if path is None else path)
        # Pre-read shows one DTC (1902FF), then the clear (14FFFFFF) succeeds.
        t = FakeTerminal({"1902FF": _ok("5902FF" + "0123002F"), "14FFFFFF": _ok("54")})
        await dtc.mode_dtc_clear(t, 0x7E4, group=0xFFFFFF, protocol="uds", log=True, label="fixed")
        out = capsys.readouterr().out
        assert "cleared" in out.lower()
        assert "P0123-00" in out  # showed what was cleared

        data = dtc_log.load_log(logp)
        clears = data.get("clears", [])
        assert len(clears) == 1
        assert clears[0]["type"] == "manual"
        assert clears[0]["group"] == "0xFFFFFF"
        assert clears[0]["codes"] == ["P0123-00"]
        assert clears[0]["label"] == "fixed"
        # A clean post-clear baseline scan is recorded so a later scan of the same
        # ECU won't also report these as self-cleared.
        bms_scans = [s for s in data["scans"] if s["scope"] == "BMS (0x7E4)"]
        assert bms_scans and bms_scans[-1]["ecus"]["BMS (0x7E4)"]["dtcs"] == []


class TestScanAll:
    @pytest.mark.asyncio
    async def test_scan_all_json(self, monkeypatch, capsys):
        # Registry: a UDS ECU (BCM 0x7A0) and a KWP ECU (BMS 0x7E4).
        monkeypatch.setattr(
            dtc,
            "resolve_protocol",
            lambda proto, tx: "kwp" if tx == 0x7E4 else "uds",
        )
        monkeypatch.setattr(
            "canlib.ecus.load_ecus",
            lambda: {
                0x7A0: {"name": "BCM", "id_protocol": "UDS"},
                0x7E4: {"name": "BMS", "id_protocol": "KWP2000"},
            },
        )
        t = FakeTerminal(
            {
                "1902FF": _ok("5902FF" + "0123002F"),  # BCM: 1 DTC
                "1800FF00": _ok("5800"),  # BMS: clean
            }
        )
        await dtc.mode_dtc_scan_all(t, as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["scanned"] == 2
        assert data["with_dtcs"] == 1
        assert data["total_codes"] == 1
        assert any(r.get("count") == 1 for r in data["results"])
        assert any(r["protocol"] == "kwp" for r in data["results"])

    @pytest.mark.asyncio
    async def test_scan_all_text_no_dtcs(self, monkeypatch, capsys):
        monkeypatch.setattr(dtc, "resolve_protocol", lambda proto, tx: "uds")
        monkeypatch.setattr(
            "canlib.ecus.load_ecus",
            lambda: {0x7A0: {"name": "BCM"}, 0x770: {"name": "IGPM"}},
        )
        t = FakeTerminal({"1902FF": _ok("5902FF")})  # both clean
        await dtc.mode_dtc_scan_all(t, as_json=False)
        out = capsys.readouterr().out
        assert "No DTCs on any" in out

    def test_classify(self):
        assert dtc._classify({"count": 1, "dtcs": [{}]}) == "faulty"
        assert dtc._classify({"count": 0, "dtcs": []}) == "clean"
        assert dtc._classify({"nrc": "0x11"}) == "nrc"
        assert dtc._classify({"error": "NO DATA"}) == "no_response"

    @pytest.mark.asyncio
    async def test_scan_all_reports_no_response_separately(self, monkeypatch, capsys):
        monkeypatch.setattr(dtc, "resolve_protocol", lambda proto, tx: "uds")
        monkeypatch.setattr(
            "canlib.ecus.load_ecus",
            lambda: {0x7A0: {"name": "BCM"}, 0x770: {"name": "IGPM"}},
        )
        # BCM clean; IGPM never answers (not in table).
        t = FakeTerminal({"1902FF": _ok("5902FF")})
        await dtc.mode_dtc_scan_all(t, as_json=True, timeout=0.1, retry=False)
        data = json.loads(capsys.readouterr().out)
        # IGPM answers 1902FF too (shared request) -> to isolate, assert counts:
        assert data["clean"] + len(data["no_response"]) == 2

    @pytest.mark.asyncio
    async def test_scan_all_retry_recovers_slow_ecu(self, monkeypatch, capsys):
        monkeypatch.setattr(dtc, "resolve_protocol", lambda proto, tx: "uds")
        monkeypatch.setattr("canlib.ecus.load_ecus", lambda: {0x7A0: {"name": "BCM"}})
        # NO DATA on first read, a DTC once retried (after the wake).
        t = FlakyTerminal({"1902FF": "5902FF0123002F"})
        await dtc.mode_dtc_scan_all(t, as_json=True, timeout=0.1, retry=True)
        data = json.loads(capsys.readouterr().out)
        assert data["no_response"] == []  # recovered on retry
        assert data["with_dtcs"] == 1
        assert "1001" in t.sent  # wake was attempted

    @pytest.mark.asyncio
    async def test_scan_all_no_retry_leaves_no_response(self, monkeypatch, capsys):
        monkeypatch.setattr(dtc, "resolve_protocol", lambda proto, tx: "uds")
        monkeypatch.setattr("canlib.ecus.load_ecus", lambda: {0x7A0: {"name": "BCM"}})
        t = FlakyTerminal({"1902FF": "5902FF0123002F"})
        await dtc.mode_dtc_scan_all(t, as_json=True, timeout=0.1, retry=False)
        data = json.loads(capsys.readouterr().out)
        assert data["no_response"] == ["BCM (0x7A0)"]
        assert "1001" not in t.sent  # no retry -> no wake

    @pytest.mark.asyncio
    async def test_scan_all_dispatch_logs_by_default(self, monkeypatch, tmp_path, capsys):
        # No --log flag in the args: dispatch must log by default (dtc_log unset
        # -> getattr default True).
        from canlib import dtc_log
        from canlib.commands._live import CANAIR_DEFAULTS, dispatch_mode

        logp = tmp_path / "dtc_log.yaml"
        monkeypatch.setattr(dtc_log, "log_path", lambda path=None: logp if path is None else path)
        monkeypatch.setattr(dtc, "resolve_protocol", lambda proto, tx: "uds")
        monkeypatch.setattr("canlib.ecus.load_ecus", lambda: {0x7A0: {"name": "BCM"}})
        t = FakeTerminal({"1902FF": _ok("5902FF" + "0123002F")})  # BCM: 1 DTC
        args = argparse.Namespace(**{**CANAIR_DEFAULTS, "dtc_all": True, "mask": "FF"})
        assert not hasattr(args, "dtc_log")  # nothing opted in
        await dispatch_mode(args, t, {}, "1.2.3.4")
        out = capsys.readouterr().out
        assert "P0123-00" in out
        assert logp.exists()  # recorded without any flag
        assert len(dtc_log.load_log(logp)["scans"]) == 1


class TestModeClear:
    @pytest.mark.asyncio
    async def test_clear_ok(self, capsys):
        t = FakeTerminal({"14FFFFFF": _ok("54")})
        await dtc.mode_dtc_clear(t, 0x7E4, group=0xFFFFFF, protocol="uds", as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["cleared"] is True
        assert data["group"] == "0xFFFFFF"
        assert t.sent == ["14FFFFFF"]

    @pytest.mark.asyncio
    async def test_clear_kwp_uses_2byte_group(self, capsys):
        t = FakeTerminal({"14FFFF": _ok("54")})
        await dtc.mode_dtc_clear(t, 0x7E4, group=0xFFFFFF, protocol="kwp", as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["cleared"] is True
        assert data["group"] == "0xFFFF"
        assert t.sent == ["14FFFF"]

    @pytest.mark.asyncio
    async def test_clear_nrc(self, capsys):
        t = FakeTerminal(
            {"14FFFFFF": {"ok": False, "nrc": 0x22, "nrc_desc": "conditionsNotCorrect"}}
        )
        await dtc.mode_dtc_clear(t, 0x7E4, protocol="uds", as_json=True)
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
        from canlib.commands._live import CANAIR_DEFAULTS

        # Routing tests: default --no-log (dtc_log=False) so they never touch the
        # real profile's dtc_log.yaml. Logging-by-default is covered separately.
        return argparse.Namespace(**{**CANAIR_DEFAULTS, "dtc_log": False, **kw})

    @pytest.mark.asyncio
    async def test_dispatch_read(self, capsys):
        from canlib.commands._live import dispatch_mode

        t = FakeTerminal({"1902FF": _ok("5902FF" + "0123002F")})
        args = self._args(dtc="7E4", mask="FF", protocol="uds", clear=False)
        await dispatch_mode(args, t, {}, "1.2.3.4")
        assert t.sent == ["1902FF"]
        assert "P0123-00" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_dispatch_clear_with_yes(self, capsys):
        from canlib.commands._live import dispatch_mode

        t = FakeTerminal({"14FFFFFF": _ok("54")})
        args = self._args(dtc="7E4", clear=True, group="FFFFFF", protocol="uds", yes=True)
        await dispatch_mode(args, t, {}, "1.2.3.4")
        assert t.sent == ["14FFFFFF"]
        assert "cleared" in capsys.readouterr().out.lower()
