"""One marker, one meaning across the sweeps (`+` answered, `~` exists, `-` nothing, `!` failed)."""

from __future__ import annotations

import re

import pytest

from canair.cli.profile_context import active
from canair.live.scan.discovery import DiscoveryProbe, scan_ecu
from canair.live.scan.sessions import scan_ecu_sessions
from canair.render import discover as discover_render
from tests.support.fakes import FakeTerminal, nrc, ok


def _probe() -> DiscoveryProbe:
    def classify(resp):
        if resp.get("ok"):
            return "positive", None
        code = resp.get("nrc")
        if code == 0x22:
            return "exists", code
        if code == 0x31:
            return "absent", code
        return "error", None

    return DiscoveryProbe(
        name="Test",
        scan_type="test",
        id_label="DID",
        id_width=1,
        service=0x2F,
        probe=lambda t, i: t.send_uds(f"2F{i:02X}00"),
        classify=classify,
        make_hit=lambda *a, **__: a,
        request_display=lambda i: f"2F {i:02X} 00",
        write_hit=None,
    )


@pytest.mark.asyncio
async def test_discovery_scan_marks_hits_and_absences(capsys):
    term = FakeTerminal(
        {"2F0000": ok("6F00"), "2F0100": nrc(0x22), "2F0200": nrc(0x31)},
        default={"ok": False, "error": "NO DATA", "raw": "NO DATA"},
    )
    await scan_ecu(
        term, _probe(), "BMS", 0x7E4, (0x00, 0x03), throttle_ms=0, verbose=True, profile=active()
    )
    out = capsys.readouterr().out
    assert re.search(r"^\s+\+ 0x00: positive", out, re.M)
    assert re.search(r"^\s+~ 0x01: exists \(NRC 0x22", out, re.M)
    assert re.search(r"^\s+- 0x02: absent \(NRC 0x31\)", out, re.M)
    assert re.search(r"^\s+! 0x03: NO DATA", out, re.M)


@pytest.mark.asyncio
async def test_sessions_scan_uses_the_same_markers(capsys):
    term = FakeTerminal(
        {"1001": ok("5001"), "1003": nrc(0x7F), "1081": nrc(0x12)},
        default={"ok": False, "error": "NO DATA", "raw": "NO DATA"},
        session_ok=False,
    )
    await scan_ecu_sessions(
        term,
        "BMS",
        0x7E4,
        (0x01, 0x03, 0x81, 0x83),
        throttle_ms=0,
        verbose=True,
        write_yaml=False,
        profile=active(),
    )
    out = capsys.readouterr().out
    assert re.search(r"^\s+\+ 10 01: supported", out, re.M)
    assert re.search(r"^\s+~ 10 03: exists, rejected in the active session", out, re.M)
    assert re.search(r"^\s+- 10 81: not supported", out, re.M)
    assert re.search(r"^\s+! 10 83:", out, re.M)


def test_the_discover_markers_never_use_a_question_mark():
    """`?` means "unverified" in a signal table, so no sweep row uses it for "no answer"."""
    marks = {
        discover_render.POSITIVE[0],
        discover_render.REFUSED[0],
        discover_render.NO_ANSWER[0],
        discover_render.PROBE_FAILED[0],
    }
    assert marks == {"+", "~", "-", "!"}
    assert "?" not in marks
