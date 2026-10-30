"""Tests for canlib.modes.kwp_iocontrol_scan — KWP2000 IOControl (0x30) scanner."""

from __future__ import annotations

import pytest
import yaml

from canlib.modes.kwp_iocontrol_scan import (
    IOCP_FREEZE_CURRENT_STATE,
    IOCP_LONG_TERM_ADJ,
    IOCP_REPORT_CURRENT_STATE,
    IOCP_RESET_TO_DEFAULT,
    IOCP_RETURN_CONTROL,
    IOCP_SHORT_TERM_ADJ,
    KWP_IOCONTROL_PROBE,
    NISSAN_IOCONTROL_PROBE,
    KwpIOControlHit,
    classify,
    mode_kwp_iocontrol_scan,
    probe_kwp_iocontrol,
    probe_nissan_iocontrol,
)
from canlib.pids_edit import append_iocontrol_discoveries_block
from tests._fakes import FakeTerminal, nrc
from tests._fakes import ok as _ok

# ── safety guard ─────────────────────────────────────────────────────────────


def test_iocp_constants_follow_the_kwp2000_table_not_uds():
    # SSF 14230-3 Issue 2, Table 9.1.3.2 — not the UDS 0x2F values (01 reset /
    # 02 freeze / 03 shortTermAdjustment), which the module once used.
    assert IOCP_RETURN_CONTROL == 0x00
    assert IOCP_REPORT_CURRENT_STATE == 0x01
    assert IOCP_RESET_TO_DEFAULT == 0x04
    assert IOCP_FREEZE_CURRENT_STATE == 0x05
    assert IOCP_SHORT_TERM_ADJ == 0x07
    assert IOCP_LONG_TERM_ADJ == 0x08


@pytest.mark.asyncio
async def test_probe_refuses_every_iocp_but_return_control():
    # Even the SSF "report" values: these ECUs are not guaranteed SSF-conformant.
    for refused in (
        IOCP_REPORT_CURRENT_STATE,
        0x02,
        0x03,
        IOCP_RESET_TO_DEFAULT,
        IOCP_FREEZE_CURRENT_STATE,
        IOCP_SHORT_TERM_ADJ,
        IOCP_LONG_TERM_ADJ,
        0xFF,
    ):
        with pytest.raises(ValueError, match="only sends 0x00"):
            await probe_kwp_iocontrol(terminal=None, lid=0x0A, iocp=refused)


@pytest.mark.asyncio
async def test_probe_rejects_out_of_range_lid():
    with pytest.raises(ValueError, match="single byte"):
        await probe_kwp_iocontrol(terminal=None, lid=0x100)


@pytest.mark.asyncio
async def test_probe_sends_30_lid_00_and_validates_echo():
    term = FakeTerminal({"300A00": _ok("700A00")})  # positive, echoing the LID
    resp = await probe_kwp_iocontrol(term, lid=0x0A)
    assert term.sent == ["300A00"]
    assert term.uds_kwargs[0]["expected_sid"] == 0x30
    assert resp["ok"] is True


@pytest.mark.asyncio
async def test_probe_flags_lid_echo_mismatch():
    # Stale frame: echoes the *previous* LID (0x09) not the requested 0x0A.
    term = FakeTerminal({"300A00": _ok("700900")})
    resp = await probe_kwp_iocontrol(term, lid=0x0A)
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


# ── Nissan: function 01 probe inside 10 C0 ───────────────────────────────────


@pytest.mark.asyncio
async def test_nissan_probe_asks_whether_value_00_is_supported():
    term = FakeTerminal({"30690100": _ok("70690100")})
    resp = await probe_nissan_iocontrol(term, lid=0x69)
    # Never 30 69 00 (the start of a one-shot command on a Nissan BCM).
    assert term.sent == ["30690100"]
    assert term.uds_kwargs[0]["expected_sid"] == 0x30
    assert resp["ok"] is True


def test_nissan_probe_config():
    assert NISSAN_IOCONTROL_PROBE.service == 0x30
    assert NISSAN_IOCONTROL_PROBE.request_display(0x07) == "30 07 01 00"


def test_classify_kwp_wrong_session_is_not_a_hit():
    # Nissan answers 0x80 outside 10 C0; as "exists" every LID would be a hit.
    assert classify({"ok": False, "nrc": 0x80}) == ("wrong-session", 0x80)


def _pids(quirks=None):
    data = {"ecus": {"BCM": {"tx_id": 0x745}}}
    if quirks:
        data["quirks"] = quirks
    return data


@pytest.mark.asyncio
async def test_nissan_profile_scans_with_the_nissan_probe_inside_c0():
    term = FakeTerminal(default=nrc(0x31))
    await mode_kwp_iocontrol_scan(
        term,
        _pids(["nissan_c0_session"]),
        ["BCM"],
        lid_range=(0x07, 0x08),
        throttle_ms=0,
        write_yaml=False,
    )
    assert term.sessions == [(False, "C0")]
    assert term.sent == ["30070100", "30080100"]


@pytest.mark.asyncio
async def test_an_explicit_session_mode_still_wins_on_nissan():
    term = FakeTerminal(default=nrc(0x31))
    await mode_kwp_iocontrol_scan(
        term,
        _pids(["nissan_c0_session"]),
        ["BCM"],
        lid_range=(0x07, 0x07),
        throttle_ms=0,
        write_yaml=False,
        session_mode="81",
    )
    assert term.sessions == [(False, "81")]
    assert term.sent == ["30070100"]


@pytest.mark.asyncio
async def test_other_profiles_keep_the_iso_probe_and_lazy_session():
    term = FakeTerminal(default=nrc(0x31))
    await mode_kwp_iocontrol_scan(
        term,
        _pids(),
        ["BCM"],
        lid_range=(0x07, 0x07),
        throttle_ms=0,
        write_yaml=False,
        confirmed=True,
    )
    assert term.sessions == []
    assert term.sent == ["300700"]
