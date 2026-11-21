"""Synthetic SecurityAccess traffic never enters traces, timing or count learning."""

import pytest

from canair.safety.blocklist import SessionPolicy
from canair.transport.elm327.wican import WiCANTerminal
from canair.uds.protocol import UDS
from tests.support.elm_ws import term_prog


@pytest.mark.asyncio
async def test_security_exchange_suppresses_wire_logs_and_timing(capsys, monkeypatch):
    terminal: WiCANTerminal = term_prog(["OK\r>", "OK\r>", "67 01 12 34\r>", "67 02 34\r>"])
    terminal._channel.verbose = True
    terminal.session_policy = SessionPolicy(protocols={0x720: UDS})
    logged = []
    monkeypatch.setattr(
        "canair.transport.elm327.terminal.log_response", lambda *a: logged.append(a)
    )
    await terminal.set_header(0x720)
    logged.clear()
    seed = await terminal.send_uds("2701", expected_sid=0x27, expected_echo=b"\x01")
    assert seed["ok"]
    terminal.session_policy = terminal.session_policy.granting_security(0x720, "2702A1B2")
    result = await terminal.send_uds("2702A1B2", expected_sid=0x27, expected_echo=b"\x02")
    assert result["ok"]
    output = capsys.readouterr()
    assert "1234" not in output.out + output.err
    assert "A1B2" not in output.out + output.err
    assert not logged
    assert not terminal.timings.snapshot()
    assert "2701" not in str(terminal.frame_counts)
    assert "A1B2" not in str(terminal.frame_counts)


@pytest.mark.asyncio
async def test_security_no_retry_even_when_requested():
    terminal = term_prog(["OK\r>", "OK\r>", "NO DATA\r>", "67 01 12 34\r>"])
    await terminal.set_header(0x720)
    result = await terminal.send_uds("2701", expected_sid=0x27, expected_echo=b"\x01", retries=3)
    assert not result["ok"]
    assert [s for s in terminal.ws.sent if s.startswith("2701")] == ["2701\r"]


def test_reset_diagnostics_do_not_expose_sensitive_request_or_reply(monkeypatch):
    terminal = term_prog([])
    terminal._security_seen = True
    events = []
    monkeypatch.setattr(
        "canair.transport.elm327.terminal.log_event", lambda *a, **kw: events.append(a)
    )
    terminal._check_for_reset("2702A1B2", "ELM327 v1.5 67011234")
    assert events
    assert "A1B2" not in str(events) and "1234" not in str(events)


@pytest.mark.asyncio
async def test_late_seed_is_not_exposed_in_next_normal_response():
    terminal = term_prog(["OK\r>", "OK\r>", "67011234\r>", "67011234\r6101ABCD\r>"])
    await terminal.set_header(0x720)
    assert (await terminal.send_uds("2701", expected_sid=0x27, expected_echo=b"\x01"))["ok"]
    result = await terminal.send_uds("2101", expected_sid=0x21, expected_echo=b"\x01")
    assert result["ok"] and result["hex"] == "6101ABCD"
    assert "1234" not in str(result)
