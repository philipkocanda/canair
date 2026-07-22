"""Tests for canlib.modes.kwp_routines_scan and the KWP/UDS routine-scan split."""

from __future__ import annotations

import pytest
import yaml

from canlib.commands._live import split_ecus_by_protocol
from canlib.modes.kwp_routines_scan import (
    KWP_ROUTINES_PROBE,
    KwpRoutineHit,
    classify,
    probe_kwp_routine_results,
)
from canlib.pids_edit import append_routines_block


# ── probe: 0x33 read-only, never 0x31 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_sends_33_lid_and_validates_echo():
    sent = {}

    class FakeTerminal:
        async def send_uds(self, req, timeout=2.0, expected_sid=None, **kw):
            sent["req"] = req
            sent["expected_sid"] = expected_sid
            return {"ok": True, "hex": "730A00", "bytes": bytes([0x73, 0x0A, 0x00])}

    resp = await probe_kwp_routine_results(FakeTerminal(), lid=0x0A)
    assert sent["req"] == "330A"  # 0x33 RequestRoutineResults — NOT 0x31 StartRoutine
    assert not sent["req"].startswith("31")
    assert sent["expected_sid"] == 0x33
    assert resp["ok"] is True


@pytest.mark.asyncio
async def test_probe_flags_lid_echo_mismatch():
    class FakeTerminal:
        async def send_uds(self, req, timeout=2.0, expected_sid=None, **kw):
            return {"ok": True, "hex": "730900", "bytes": bytes([0x73, 0x09, 0x00])}

    resp = await probe_kwp_routine_results(FakeTerminal(), lid=0x0A)
    assert resp["ok"] is False
    assert "echo mismatch" in resp["error"]


@pytest.mark.asyncio
async def test_probe_rejects_out_of_range_lid():
    with pytest.raises(ValueError, match="single byte"):
        await probe_kwp_routine_results(terminal=None, lid=0x100)


# ── classify() ───────────────────────────────────────────────────────────────


def test_classify():
    assert classify({"ok": True}) == ("positive", None)
    assert classify({"ok": False, "nrc": 0x11}) == ("service-absent", 0x11)
    assert classify({"ok": False, "nrc": 0x31}) == ("absent", 0x31)
    assert classify({"ok": False, "nrc": 0x24})[0] == "exists"  # never run yet
    assert classify({"ok": False, "nrc": 0x22})[0] == "exists"
    assert classify({"ok": False, "nrc": 0x7F}) == ("wrong-session", 0x7F)
    assert classify({"ok": False, "error": "timeout"}) == ("error", None)


def test_probe_config():
    assert KWP_ROUTINES_PROBE.service == 0x33
    assert KWP_ROUTINES_PROBE.id_width == 1
    assert KWP_ROUTINES_PROBE.scan_type == "routines-kwp"
    assert KWP_ROUTINES_PROBE.request_display(0x0A) == "33 0A"


# ── writeback: 2-hex-digit routine LID keys, quoted, round-trip ──────────────


def test_writeback_two_digit_lid_keys(tmp_path):
    (tmp_path / "test.yaml").write_text("TEST:\n  tx_id: 0x7E4\n")
    hits = [
        KwpRoutineHit(0x80, "extended", "738000", None, None),
        KwpRoutineHit(0x0A, "extended", "", 0x24, "requestSequenceError"),
    ]
    path = append_routines_block("TEST", hits, pids_dir=tmp_path, key_width=2)
    raw = path.read_text()
    assert '"80":' in raw and '"0A":' in raw
    data = yaml.safe_load(raw)
    rts = data["TEST"]["routines"]
    assert set(rts.keys()) == {"80", "0A"}
    assert rts["80"]["response"] == "738000"
    assert rts["0A"]["nrc"] == 0x24


# ── safety boundary: BMS (KWP2000) never routed to the UDS 0x31 scanner ──────


def test_split_ecus_by_protocol():
    # Against the bundled ioniq-2017 profile: BMS is KWP2000, IGPM is UDS.
    uds, kwp = split_ecus_by_protocol(["BMS", "IGPM"])
    assert "BMS" in kwp  # → 0x33 results path, never 0x31 StartRoutine
    assert "IGPM" in uds  # → UDS 0x31 SF03


def test_split_unknown_ecu_defaults_to_uds():
    uds, kwp = split_ecus_by_protocol(["NOPE_NOT_AN_ECU"])
    assert uds == ["NOPE_NOT_AN_ECU"]
    assert kwp == []
