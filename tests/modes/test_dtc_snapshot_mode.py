"""Tests for the DTC snapshot step of ``canair dtc`` (`canlib.modes.dtc.snapshots`).

The CCM replies are the Ioniq's real ones (DTC C182C, records 01 and 02, DID 4710).
"""

import json
from contextlib import contextmanager

import pytest

from canlib.modes import dtc
from canlib.modes.dtc import snapshots as snap_mode
from tests.support.fakes import FakeTerminal, nrc
from tests.support.fakes import ok as _ok
from tests.support.live_args import live_args

CCM = 0x733
CODES = _ok("590209" + "582C0008")
IDS = _ok("5903582C0001582C0002")
REC_01 = _ok("5904582C000801014710021AA7207C0AFFFFFF")
REC_02 = _ok("5904582C000802014710C02E6736FC0AFFFFF8")
CCM_REPLIES = {"1902FF": CODES, "1903": IDS, "1904582C0001": REC_01, "1904582C0002": REC_02}

# A profile defining DID 4710's first two bytes: enough to see decoding happen.
PIDS_DATA = {
    "ecus": {
        "CCM": {
            "tx_id": CCM,
            "pids": {
                "224710": {
                    "status": "draft",
                    "snapshot_only": True,
                    "snapshot_length": 9,
                    "parameters": {
                        "SNAP_EV_READY": {"expression": "B05:7"},
                        "SNAP_PROGRESS_BYTE": {"expression": "B09", "unit": "raw"},
                    },
                }
            },
        }
    }
}


@pytest.fixture(autouse=True)
def _ccm_name(monkeypatch):
    monkeypatch.setattr(
        "canlib.ecus.ecu_name", lambda tx, *a, **k: {CCM: "CCM"}.get(tx, f"X{tx:03X}")
    )


class TestRead:
    @pytest.mark.asyncio
    async def test_snapshots_are_read_and_decoded_by_default(self, capsys):
        t = FakeTerminal(CCM_REPLIES)
        await dtc.mode_dtc_read(t, CCM, protocol="uds", as_json=True, pids_data=PIDS_DATA)
        assert t.sent == ["1902FF", "1903", "1904582C0001", "1904582C0002"]
        code = json.loads(capsys.readouterr().out)["dtcs"][0]
        assert [s["record"] for s in code["snapshots"]] == [1, 2]
        did = code["snapshots"][1]["dids"][0]
        assert did["did"] == "4710" and did["data"] == "C02E6736FC0AFFFFF8"
        values = {s["name"]: s["value"] for s in did["signals"]}
        # 62 47 10 C0 2E 67 | 36: B05 = 0xC0 (bit 7 set), B09 = 0x36 = 54 (B08 is a PCI byte).
        assert values == {"SNAP_EV_READY": 1, "SNAP_PROGRESS_BYTE": 54}

    @pytest.mark.asyncio
    async def test_requests_are_echo_validated(self):
        t = FakeTerminal(CCM_REPLIES)
        await dtc.mode_dtc_read(t, CCM, protocol="uds", pids_data=PIDS_DATA)
        by_request = dict(zip(t.sent, t.uds_kwargs, strict=True))
        assert by_request["1903"]["expected_echo"] == bytes([0x03])
        assert by_request["1904582C0001"]["expected_echo"] == bytes.fromhex("04582C00")

    @pytest.mark.asyncio
    async def test_no_snapshots_reads_only_the_codes(self):
        t = FakeTerminal(CCM_REPLIES)
        await dtc.mode_dtc_read(t, CCM, protocol="uds", snapshots=False, pids_data=PIDS_DATA)
        assert t.sent == ["1902FF"]

    @pytest.mark.asyncio
    async def test_a_clean_ecu_is_not_asked(self):
        t = FakeTerminal({"1902FF": _ok("590209")})
        await dtc.mode_dtc_read(t, CCM, protocol="uds", pids_data=PIDS_DATA)
        assert t.sent == ["1902FF"]

    @pytest.mark.asyncio
    async def test_a_kwp_ecu_is_not_asked(self):
        t = FakeTerminal({"1800FF00": _ok("5801" + "0123" + "10")})
        await dtc.mode_dtc_read(t, 0x7E4, protocol="kwp")
        assert t.sent == ["1800FF00"]

    @pytest.mark.asyncio
    async def test_a_refused_listing_is_reported_not_raised(self, capsys):
        t = FakeTerminal({"1902FF": CODES, "1903": nrc(0x12, "subFunctionNotSupported")})
        await dtc.mode_dtc_read(t, CCM, protocol="uds", as_json=True, pids_data=PIDS_DATA)
        data = json.loads(capsys.readouterr().out)
        assert data["snapshot_status"] == {
            "status": "unsupported",
            "detail": "NRC 0x12 (subFunctionNotSupported)",
        }
        assert "snapshots" not in data["dtcs"][0]

    @pytest.mark.asyncio
    async def test_text_output_lists_records_under_the_code(self, capsys):
        t = FakeTerminal(CCM_REPLIES)
        await dtc.mode_dtc_read(t, CCM, protocol="uds", pids_data=PIDS_DATA)
        out = capsys.readouterr().out
        assert "snapshot 01  DID 4710" in out and "snapshot 02  DID 4710" in out
        assert "SNAP_PROGRESS_BYTE  54 raw" in out

    @pytest.mark.asyncio
    async def test_an_undefined_did_shows_raw_data_and_how_to_define_it(self, capsys):
        t = FakeTerminal(CCM_REPLIES)
        await dtc.mode_dtc_read(t, CCM, protocol="uds", pids_data={"ecus": {}})
        out = capsys.readouterr().out
        assert "not defined (224710):" in out and "021AA7207C0AFFFFFF" in out
        assert "canair pids set-snapshot-only CCM 224710 --length 9" in out
        assert out.count("Snapshot DID 4710 is not defined") == 1

    @pytest.mark.asyncio
    async def test_a_failed_record_read_keeps_the_others(self, capsys):
        replies = {**CCM_REPLIES, "1904582C0001": nrc(0x31, "requestOutOfRange")}
        t = FakeTerminal(replies)
        await dtc.mode_dtc_read(t, CCM, protocol="uds", as_json=True, pids_data=PIDS_DATA)
        snaps = json.loads(capsys.readouterr().out)["dtcs"][0]["snapshots"]
        assert snaps[0] == {"record": 1, "error": "NRC 0x31 (requestOutOfRange)"}
        assert snaps[1]["dids"][0]["did"] == "4710"

    @pytest.mark.asyncio
    async def test_a_reply_for_another_record_is_an_error(self, capsys):
        # The echo covers the DTC only: record 02's reply must not decode, log or save as 01.
        replies = {**CCM_REPLIES, "1904582C0001": REC_02}
        t = FakeTerminal(replies)
        await dtc.mode_dtc_read(t, CCM, protocol="uds", as_json=True, pids_data=PIDS_DATA)
        first = json.loads(capsys.readouterr().out)["dtcs"][0]["snapshots"][0]
        assert first == {"record": 1, "error": "reply is for record 02, not 01"}


class _Pressed:
    active = True
    pressed = True


class TestSweep:
    def _registry(self, monkeypatch, tx_ids):
        monkeypatch.setattr("canlib.ecus.load_ecus", lambda: {tx: {} for tx in tx_ids})
        monkeypatch.setattr("canlib.modes.dtc.reading.resolve_protocol", lambda proto, tx: "uds")

    @pytest.mark.asyncio
    async def test_snapshots_are_a_final_step(self, monkeypatch, capsys):
        self._registry(monkeypatch, [0x7A0, CCM])
        t = FakeTerminal(CCM_REPLIES)
        await dtc.mode_dtc_scan_all(t, as_json=True, retry=False, pids_data=PIDS_DATA)
        # Every ECU's codes are read before any snapshot request.
        assert t.sent[:2] == ["1902FF", "1902FF"]
        assert "1903" not in t.sent[:2]
        data = json.loads(capsys.readouterr().out)
        assert data["snapshots"]["records_read"] == 4  # two ECUs answer with the CCM replies
        assert data["snapshots"]["skipped"] is False

    @pytest.mark.asyncio
    async def test_a_refusing_ecu_is_asked_once(self, monkeypatch, capsys):
        self._registry(monkeypatch, [CCM])
        codes = _ok("590209" + "582C0008" + "582B0008")
        t = FakeTerminal({"1902FF": codes, "1903": nrc(0x12)})
        await dtc.mode_dtc_scan_all(t, as_json=True, retry=False, pids_data=PIDS_DATA)
        assert t.sent.count("1903") == 1
        assert json.loads(capsys.readouterr().out)["snapshots"]["unsupported"] == {
            "0x733": "NRC 0x12"
        }

    @pytest.mark.asyncio
    async def test_a_keypress_skips_the_rest_and_the_codes_stay(self, monkeypatch, capsys):
        self._registry(monkeypatch, [CCM])

        @contextmanager
        def pressed(enabled=True):
            yield _Pressed()

        monkeypatch.setattr("canlib.tui.skip_key.skip_listener", pressed)
        t = FakeTerminal(CCM_REPLIES)
        await dtc.mode_dtc_scan_all(t, retry=False, pids_data=PIDS_DATA)
        assert "1904582C0001" not in t.sent
        out = capsys.readouterr().out
        assert "Snapshot step skipped" in out
        assert "C182C-00" in out
        assert "press s or esc to skip" in out

    @pytest.mark.asyncio
    async def test_no_snapshots_skips_the_step(self, monkeypatch):
        self._registry(monkeypatch, [CCM])
        t = FakeTerminal(CCM_REPLIES)
        await dtc.mode_dtc_scan_all(t, as_json=True, retry=False, snapshots=False)
        assert t.sent == ["1902FF"]


class TestSkipListener:
    def test_inert_off_a_tty(self, monkeypatch):
        from canlib.tui.skip_key import skip_listener

        monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
        with skip_listener() as keys:
            assert not keys.active and not keys.pressed

    def test_disabled_is_inert(self):
        from canlib.tui.skip_key import skip_listener

        with skip_listener(enabled=False) as keys:
            assert not keys.active and not keys.pressed


class TestDispatch:
    @pytest.mark.asyncio
    async def test_runs_through_the_dtc_handler_with_the_profile(self, capsys):
        from canlib.commands._live.handlers.diagnostics import handle_dtc

        args = live_args(
            dtc_log=False, dtc="733", mask="FF", protocol="uds", clear=False, json=True
        )
        t = FakeTerminal(CCM_REPLIES)
        await handle_dtc(args, t, PIDS_DATA, "1.2.3.4")
        assert "1904582C0002" in t.sent
        code = json.loads(capsys.readouterr().out)["dtcs"][0]
        assert code["snapshots"][0]["dids"][0]["signals"]

    @pytest.mark.asyncio
    async def test_no_snapshots_flag(self):
        from canlib.commands._live.handlers.diagnostics import handle_dtc

        args = live_args(
            dtc_log=False,
            dtc_snapshots=False,
            dtc="733",
            mask="FF",
            protocol="uds",
            clear=False,
        )
        t = FakeTerminal(CCM_REPLIES)
        await handle_dtc(args, t, PIDS_DATA, "1.2.3.4")
        assert t.sent == ["1902FF"]


def test_wants_snapshots():
    assert snap_mode.wants_snapshots({"protocol": "uds", "dtcs": [{}]})
    assert not snap_mode.wants_snapshots({"protocol": "uds", "dtcs": []})
    assert not snap_mode.wants_snapshots({"protocol": "kwp", "dtcs": [{}]})


@pytest.fixture
def logp(monkeypatch, tmp_path):
    from canlib.dtc import log as dtc_log

    path = tmp_path / "dtc_log.yaml"
    monkeypatch.setattr(dtc_log, "log_path", lambda p=None: path if p is None else p)
    return path


def _ccm_pids(monkeypatch):
    """Decode logged snapshots with PIDS_DATA's CCM definitions."""
    from canlib.pids import build_ecu_index

    pids = build_ecu_index(PIDS_DATA)["CCM"]["pids"]
    monkeypatch.setattr(
        "canlib.dtc.snapshot_decode.ecu_pids_index", lambda ecu: pids if ecu == "CCM" else {}
    )


class TestLog:
    @pytest.mark.asyncio
    async def test_the_raw_replies_are_logged_with_the_code(self, logp):
        from canlib.dtc import log as dtc_log

        t = FakeTerminal(CCM_REPLIES)
        await dtc.mode_dtc_read(t, CCM, protocol="uds", log=True, pids_data=PIDS_DATA)
        (scan,) = dtc_log.load_log(logp)["scans"]
        (info,) = scan["ecus"].values()
        assert info["snapshots"] == {
            "C182C-00": [
                {"record": 1, "hex": "5904582C000801014710021AA7207C0AFFFFFF"},
                {"record": 2, "hex": "5904582C000802014710C02E6736FC0AFFFFF8"},
            ]
        }

    @pytest.mark.asyncio
    async def test_dtc_log_decodes_them_and_notes_when_unchanged(self, logp, monkeypatch, capsys):
        from canlib.dtc import log as dtc_log

        _ccm_pids(monkeypatch)
        for _ in range(2):
            await dtc.mode_dtc_read(
                FakeTerminal(CCM_REPLIES), CCM, protocol="uds", log=True, pids_data=PIDS_DATA
            )
        capsys.readouterr()
        first, second = dtc_log.load_log(logp)["scans"]
        text = "\n".join(dtc_log.render_scan(second, first))
        assert "snapshot 02  DID 4710" in text
        assert "SNAP_PROGRESS_BYTE  54 raw" in text
        assert f"snapshots unchanged since the scan of {first['timestamp']}" in text

    def test_a_record_that_failed_is_not_logged(self):
        from canlib.modes.dtc.scan_log import logged_snapshots

        dtcs = [{"dtc": "C182C-00", "snapshots": [{"record": 1, "error": "NRC 0x31"}]}]
        assert logged_snapshots(dtcs) == {}


class TestClear:
    @pytest.mark.asyncio
    async def test_snapshots_are_read_and_logged_before_the_clear(self, logp, capsys):
        from canlib.dtc import log as dtc_log

        t = FakeTerminal({**CCM_REPLIES, "14FFFFFF": _ok("54")})
        await dtc.mode_dtc_clear(t, CCM, protocol="uds", log=True, pids_data=PIDS_DATA)
        assert t.sent == ["1902FF", "1903", "1904582C0001", "1904582C0002", "14FFFFFF"]
        (clear,) = dtc_log.load_log(logp)["clears"]
        assert [r["record"] for r in clear["snapshots"]["C182C-00"]] == [1, 2]
        assert "2 snapshot record(s) read before clearing" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_dtc_log_shows_what_the_clear_erased(self, logp, monkeypatch):
        from canlib.dtc import log as dtc_log

        _ccm_pids(monkeypatch)
        t = FakeTerminal({**CCM_REPLIES, "14FFFFFF": _ok("54")})
        await dtc.mode_dtc_clear(t, CCM, protocol="uds", log=True, pids_data=PIDS_DATA)
        clears = dtc_log.load_log(logp)["clears"]
        scan = dtc_log.build_scan("0x73B", {"0x73B": {"dtcs": []}}, timestamp="2999-01-01T00:00")
        text = "\n".join(dtc_log.render_scan(scan, None, clears))
        assert "Snapshots erased by the clear" in text and "SNAP_EV_READY" in text

    @pytest.mark.asyncio
    async def test_json_reports_the_erased_snapshots(self, logp, capsys):
        t = FakeTerminal({**CCM_REPLIES, "14FFFFFF": _ok("54")})
        await dtc.mode_dtc_clear(
            t, CCM, protocol="uds", log=True, as_json=True, pids_data=PIDS_DATA
        )
        data = json.loads(capsys.readouterr().out)
        assert list(data["erased_snapshots"]) == ["C182C-00"]

    @pytest.mark.asyncio
    async def test_without_snapshots_the_clear_reads_only_the_codes(self, logp):
        t = FakeTerminal({**CCM_REPLIES, "14FFFFFF": _ok("54")})
        await dtc.mode_dtc_clear(t, CCM, protocol="uds", log=True, snapshots=False)
        assert t.sent == ["1902FF", "14FFFFFF"]


class TestSave:
    @pytest.mark.asyncio
    async def test_replies_are_saved_under_their_own_request(self, monkeypatch):
        from canlib.modes.dtc.snapshot_capture import SESSION_NOTE

        saved = []
        monkeypatch.setattr("canlib.captures.save_session_journaled", saved.append)
        t = FakeTerminal(CCM_REPLIES)
        await dtc.mode_dtc_read(
            t, CCM, protocol="uds", pids_data=PIDS_DATA, save=True, label="fault", notes="n"
        )
        (session,) = saved
        assert session["label"] == "fault"
        assert SESSION_NOTE in session["notes"] and session["notes"].startswith("n")
        caps = session["captures"]
        assert [c["pid"] for c in caps] == ["1904582C0001", "1904582C0002"]
        assert caps[0]["payload"] == "5904582C000801014710021AA7207C0AFFFFFF"
        assert all(c.get("time") for c in caps)

    @pytest.mark.asyncio
    async def test_nothing_to_save_says_so(self, monkeypatch, capsys):
        saved = []
        monkeypatch.setattr("canlib.captures.save_session_journaled", saved.append)
        t = FakeTerminal({"1902FF": _ok("590209")})
        await dtc.mode_dtc_read(t, CCM, protocol="uds", save=True, label="x")
        assert saved == []
        assert "no snapshot replies to save" in capsys.readouterr().out


SNAPSHOT_CAPTURE = {
    "_path": "f",
    "_session_idx": 0,
    "date": "2026-11-02",
    "time": "10:00:00",
    "ecu": "CCM",
    "pid": "1904582C0002",
    "payload": "5904582C000802014710C02E6736FC0AFFFFF8",
    "session_version": "4.0.0",
}


class TestCoverage:
    def test_a_snapshot_capture_stands_in_for_a_snapshot_only_pid(self):
        from canlib.commands.coverage.payloads import select_captures

        picked = select_captures([SNAPSHOT_CAPTURE], snapshot_only={("CCM", "224710")})
        assert picked.payloads[("CCM", "224710")]["payload"] == "624710C02E6736FC0AFFFFF8"

    def test_not_for_a_pid_without_the_flag(self):
        from canlib.commands.coverage.payloads import select_captures

        assert ("CCM", "224710") not in select_captures([SNAPSHOT_CAPTURE]).payloads

    def test_the_flagged_keys_come_from_the_profile(self):
        from canlib.commands.coverage import snapshot_only_pids

        assert snapshot_only_pids(PIDS_DATA) == {("CCM", "224710")}


class TestSensitive:
    """A snapshot holding a sensitive DID is masked on screen and never logged or saved."""

    def _pids(self):
        import copy

        data = copy.deepcopy(PIDS_DATA)
        data["ecus"]["CCM"]["pids"]["224710"]["sensitive"] = True
        return data

    @pytest.mark.asyncio
    async def test_masked_not_logged_not_saved(self, logp, monkeypatch, capsys):
        from canlib.dtc import log as dtc_log
        from canlib.privacy.sensitive import MASK

        saved = []
        monkeypatch.setattr("canlib.captures.save_session_journaled", saved.append)
        t = FakeTerminal(CCM_REPLIES)
        await dtc.mode_dtc_read(
            t,
            CCM,
            protocol="uds",
            as_json=True,
            log=True,
            save=True,
            label="x",
            pids_data=self._pids(),
        )
        snap = json.loads(capsys.readouterr().out)["dtcs"][0]["snapshots"][0]
        assert snap["hex"] == MASK and snap["dids"][0]["data"] == MASK
        assert {s["value"] for s in snap["dids"][0]["signals"]} == {MASK}
        (scan,) = dtc_log.load_log(logp)["scans"]
        assert "snapshots" not in next(iter(scan["ecus"].values()))
        assert saved == []
