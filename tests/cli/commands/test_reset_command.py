"""``canair reset``: off until ``safety.allow_reset``; the ECU's name typed (or ``--yes``) first."""

from __future__ import annotations

import argparse
import asyncio
import io

import pytest

from canair.cli import build_parser
from canair.cli.commands import reset as reset_cmd
from canair.cli.live.handlers import actuators
from canair.core.prompt import PromptUnavailable
from canair.safety.blocklist import SafetyBlocked, SessionPolicy
from canair.uds.protocol import KWP, UDS
from tests.support.fakes import FakeTerminal


class _Stdin(io.StringIO):
    def __init__(self, text: str, tty: bool = True):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def live(monkeypatch):
    calls: list = []
    monkeypatch.setattr(reset_cmd, "run_live", lambda args, op: calls.append(op) or 0)
    return calls


def _parse(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["reset", *argv])


def test_off_until_the_config_key_is_true(live, capsys):
    assert reset_cmd.run(_parse("BMS", "hard", "--yes")) == 2
    assert live == []
    err = capsys.readouterr().err
    assert "error: canair reset is off: safety.allow_reset is not true" in err
    assert "canair config set safety.allow_reset true" in err


def test_runs_once_the_key_is_true(live, monkeypatch):
    monkeypatch.setattr(reset_cmd, "reset_allowed", lambda: True)
    assert reset_cmd.run(_parse("BMS", "hard")) == 0
    assert [op.handler for op in live] == [actuators.handle_reset]


@pytest.mark.parametrize("argv", [(), ("BMS",)])
def test_needs_an_ecu_and_a_type(live, argv):
    assert reset_cmd.run(_parse(*argv)) == 2
    assert live == []


def test_only_the_three_restart_types_parse():
    assert _parse("BMS", "key-off-on").reset_type == "key-off-on"
    with pytest.raises(SystemExit):
        _parse("BMS", "rapid-shutdown")


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    async def fake_reset(terminal, tx_id, reset_type, **kw):
        calls.append({"tx_id": tx_id, "reset_type": reset_type, **kw})
        return True

    monkeypatch.setattr(actuators, "mode_ecu_reset", fake_reset)
    return calls


def _handle(args, protocol=None):
    term = FakeTerminal()
    tx = actuators.resolve_tx(args.reset, profile=actuators.active())
    if protocol is not None and tx is not None:
        term.session_policy = SessionPolicy({tx: protocol})
    asyncio.run(actuators.handle_reset(args, term, {}, None))


def test_typing_the_ecu_name_sends(monkeypatch, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("BMS\n"))
    _handle(_parse("BMS", "hard"))
    assert [c["reset_type"] for c in sent] == [0x01]


@pytest.mark.parametrize("answer", ["y\n", "yes\n", "bms\n", ""])
def test_anything_else_declines(monkeypatch, capsys, sent, answer):
    monkeypatch.setattr("sys.stdin", _Stdin(answer))
    with pytest.raises(SystemExit) as exc:
        _handle(_parse("BMS", "hard"))
    assert exc.value.code == 1
    assert sent == []
    err = capsys.readouterr().err
    assert "warning: BMS (0x7E4) stops working while it restarts" in err
    assert "Type BMS to continue" in err


def test_no_terminal_needs_yes(monkeypatch, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("", tty=False))
    with pytest.raises(PromptUnavailable):
        _handle(_parse("BMS", "hard"))
    assert sent == []


def test_yes_skips_the_question(monkeypatch, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("", tty=False))
    _handle(_parse("BMS", "soft", "--yes"), protocol=UDS)
    assert [c["reset_type"] for c in sent] == [0x03]


def test_a_refused_type_is_never_asked_about(monkeypatch, capsys, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("BMS\n"))
    with pytest.raises(SafetyBlocked, match="speaks KWP2000"):
        _handle(_parse("BMS", "soft"), protocol=KWP)
    assert sent == []
    assert "Type BMS" not in capsys.readouterr().err
