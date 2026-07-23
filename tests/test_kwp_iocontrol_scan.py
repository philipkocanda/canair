"""Tests for canlib.modes.kwp_iocontrol_scan — KWP2000 IOControl (0x30) scanner."""

from __future__ import annotations

import pytest
import yaml

from canlib.modes.kwp_iocontrol_scan import (
    IOCP_FREEZE,
    IOCP_RESET_TO_DEFAULT,
    IOCP_RETURN_CONTROL,
    IOCP_SHORT_TERM_ADJ,
    KWP_IOCONTROL_PROBE,
    KwpIOControlHit,
    classify,
    probe_kwp_iocontrol,
)
from canlib.pids_edit import append_iocontrol_discoveries_block

# ── safety guard ─────────────────────────────────────────────────────────────


def test_iocp_constants():
    assert IOCP_RETURN_CONTROL == 0x00
    assert IOCP_RESET_TO_DEFAULT == 0x01
    assert IOCP_FREEZE == 0x02
    assert IOCP_SHORT_TERM_ADJ == 0x03


@pytest.mark.asyncio
async def test_probe_refuses_unsafe_iocp():
    for unsafe in (IOCP_RESET_TO_DEFAULT, IOCP_FREEZE, IOCP_SHORT_TERM_ADJ, 0x04, 0xFF):
        with pytest.raises(ValueError, match="safe"):
            await probe_kwp_iocontrol(terminal=None, lid=0x0A, iocp=unsafe)


@pytest.mark.asyncio
async def test_probe_rejects_out_of_range_lid():
    with pytest.raises(ValueError, match="single byte"):
        await probe_kwp_iocontrol(terminal=None, lid=0x100)


@pytest.mark.asyncio
async def test_probe_sends_30_lid_00_and_validates_echo():
    sent = {}

    class FakeTerminal:
        async def send_uds(self, req, timeout=2.0, expected_sid=None, **kw):
            sent["req"] = req
            sent["expected_sid"] = expected_sid
            # Positive response echoing the LID: 70 0A ...
            return {"ok": True, "hex": "700A00", "bytes": bytes([0x70, 0x0A, 0x00])}

    resp = await probe_kwp_iocontrol(FakeTerminal(), lid=0x0A)
    assert sent["req"] == "300A00"
    assert sent["expected_sid"] == 0x30
    assert resp["ok"] is True


@pytest.mark.asyncio
async def test_probe_flags_lid_echo_mismatch():
    class FakeTerminal:
        async def send_uds(self, req, timeout=2.0, expected_sid=None, **kw):
            # Stale frame: echoes the *previous* LID (0x09) not the requested 0x0A
            return {"ok": True, "hex": "700900", "bytes": bytes([0x70, 0x09, 0x00])}

    resp = await probe_kwp_iocontrol(FakeTerminal(), lid=0x0A)
    assert resp["ok"] is False
    assert "echo mismatch" in resp["error"]


# ── classify() ───────────────────────────────────────────────────────────────


def test_classify_positive():
    assert classify({"ok": True}) == ("positive", None)


def test_classify_service_absent():
    assert classify({"ok": False, "nrc": 0x11}) == ("service-absent", 0x11)


def test_classify_absent():
    assert classify({"ok": False, "nrc": 0x31}) == ("absent", 0x31)


def test_classify_exists_and_wrong_session():
    assert classify({"ok": False, "nrc": 0x22})[0] == "exists"
    assert classify({"ok": False, "nrc": 0x12})[0] == "exists"
    assert classify({"ok": False, "nrc": 0x7F}) == ("wrong-session", 0x7F)


def test_classify_error():
    assert classify({"ok": False, "error": "timeout"}) == ("error", None)


# ── probe config ─────────────────────────────────────────────────────────────


def test_probe_config():
    assert KWP_IOCONTROL_PROBE.service == 0x30
    assert KWP_IOCONTROL_PROBE.id_width == 1
    assert KWP_IOCONTROL_PROBE.scan_type == "iocontrol-kwp"
    assert KWP_IOCONTROL_PROBE.request_display(0x80) == "30 80 00"


# ── writeback: 2-hex-digit LID keys are quoted and round-trip ────────────────


def test_writeback_two_digit_lid_keys(tmp_path):
    (tmp_path / "test.yaml").write_text("TEST:\n  tx_id: 0x7E4\n")
    hits = [
        KwpIOControlHit(0x80, "extended", "708000", None, None),
        KwpIOControlHit(0x0A, "extended", "", 0x22, "conditionsNotCorrect"),
    ]
    path = append_iocontrol_discoveries_block("TEST", hits, pids_dir=tmp_path, key_width=2)
    raw = path.read_text()
    # Keys must be quoted so all-digit LIDs aren't parsed as ints/octal.
    assert '"80":' in raw
    assert '"0A":' in raw

    data = yaml.safe_load(raw)
    disc = data["TEST"]["iocontrol_discoveries"]
    # Both keys survive as 2-char hex strings (not ints).
    assert set(disc.keys()) == {"80", "0A"}
    assert disc["80"]["response"] == "708000"
    assert disc["0A"]["nrc"] == 0x22
