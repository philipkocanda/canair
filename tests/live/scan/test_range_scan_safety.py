"""``canair scan range`` asks before an actuating service and refuses 0x31 on a KWP2000 ECU."""

from __future__ import annotations

import asyncio
import io

import pytest

from canair.cli.profile_context import active
from canair.core.prompt import PromptUnavailable
from canair.live.read.exec import _exec_scan
from canair.live.scan.range import confirm_actuating_scan, mode_scan, scan_request
from canair.safety.blocklist import SafetyBlocked, SessionPolicy
from canair.transport.session_manager import SessionManager
from canair.uds.protocol import KWP, UDS, UNKNOWN
from tests.support.fakes import FakeTerminal

TX = 0x7E4


class _Stdin(io.StringIO):
    def __init__(self, text: str, tty: bool):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _terminal(protocol) -> FakeTerminal:
    term = FakeTerminal()
    term.session_policy = SessionPolicy(protocols={TX: protocol})
    return term


def _confirm(term, service, ranges, append="", **kw) -> bool:
    return confirm_actuating_scan(term, TX, service, ranges, append, profile=active(), **kw)


def test_the_request_carries_the_service_s_identifier_width():
    assert scan_request(0x22, 0xB001) == "22B001"
    assert scan_request(0x21, 0x05) == "2105"
    assert scan_request(0x2F, 0xE000, "03") == "2FE00003"


@pytest.mark.parametrize("answer", ["y\n", "n\n", ""])
def test_routine_control_is_refused_on_kwp2000_whatever_the_answer(monkeypatch, answer):
    monkeypatch.setattr("sys.stdin", _Stdin(answer, tty=True))
    with pytest.raises(SafetyBlocked, match="StartRoutineByLocalIdentifier"):
        _confirm(_terminal(KWP), 0x31, (0xF000, 0xF0FF), yes=True)


def test_mode_scan_itself_sends_no_routine_control_to_kwp2000():
    term = _terminal(KWP)
    with pytest.raises(SafetyBlocked):
        asyncio.run(mode_scan(term, TX, 0x31, (0xF000, 0xF001), False, False, profile=active()))
    assert term.sent == []


def test_an_actuating_scan_asks_and_a_decline_says_no(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _Stdin("n\n", tty=True))
    assert _confirm(_terminal(UDS), 0x31, (0xF000, 0xF0FF)) is False
    err = capsys.readouterr().err
    assert "warning: 256 of the 256 requests of this routine (0x31) scan" in err
    assert "(the first is 31F000)" in err
    assert "Aborted" in err


def test_yes_answers_the_question(monkeypatch):
    monkeypatch.setattr("sys.stdin", _Stdin("", tty=False))
    assert _confirm(_terminal(UDS), 0x2F, (0xE000, 0xE001), "03", yes=True) is True


def test_without_a_terminal_and_without_yes_it_is_refused(monkeypatch):
    monkeypatch.setattr("sys.stdin", _Stdin("y\n", tty=False))
    with pytest.raises(PromptUnavailable, match="--yes"):
        _confirm(_terminal(UNKNOWN), 0x30, (0x01, 0x02))


@pytest.mark.parametrize(
    ("service", "ranges", "append"),
    [
        (0x22, (0xB000, 0xB0FF), ""),  # a read
        (0x2F, (0xE000, 0xE0FF), "00"),  # returnControlToECU on UDS: a release
        (0x2E, (0xB000, 0xB001), "00"),  # the blocklist refuses every request anyway
    ],
)
def test_no_question_for_a_scan_that_cannot_actuate(monkeypatch, service, ranges, append):
    monkeypatch.setattr("sys.stdin", _Stdin("", tty=False))
    assert _confirm(_terminal(UDS), service, ranges, append) is True


def test_the_handler_does_not_scan_when_the_answer_is_no(monkeypatch):
    from types import SimpleNamespace

    from canair.cli.live.handlers import scans

    calls: list = []

    async def fake_scan(*a, **kw):
        calls.append(a)

    monkeypatch.setattr(scans, "mode_scan", fake_scan)
    monkeypatch.setattr(scans, "resolve_tx", lambda *_a, **_k: TX)
    monkeypatch.setattr("sys.stdin", _Stdin("n\n", tty=True))
    args = SimpleNamespace(
        tx="BMS", service="31", range="F000-F001", append=None, yes=False, verbose=False,
        json=False, session=False, wake=False, save=False, label=None, state=None, notes=None,
    )  # fmt: skip
    with pytest.raises(SystemExit) as exc:
        asyncio.run(scans.handle_scan(args, _terminal(UDS), {}, "h"))  # type: ignore[arg-type]
    assert exc.value.code == 1
    assert calls == []


def test_a_pipeline_scan_step_names_the_command_that_takes_yes(monkeypatch):
    monkeypatch.setattr("sys.stdin", _Stdin("", tty=False))
    term = _terminal(UDS)
    sm = SessionManager(term)
    with pytest.raises(PromptUnavailable, match="--yes to `canair scan range` instead"):
        asyncio.run(_exec_scan(sm, "7E4", "31", "F000-F001", "", False, profile=active()))
    assert term.sent == []
