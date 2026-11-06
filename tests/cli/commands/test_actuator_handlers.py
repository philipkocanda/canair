"""``routines --sf start`` asks first (``--yes`` answers), a KWP2000 ECU is refused before that,
and the lookups of ``io`` fail loudly."""

from __future__ import annotations

import argparse
import asyncio
import io

import pytest

from canair.cli import build_parser
from canair.cli.commands import io as io_cmd
from canair.cli.live.handlers import actuators
from canair.core.prompt import PromptUnavailable
from canair.safety.blocklist import SafetyBlocked

DATA = {
    "ecus": {
        "BCM": {
            "tx_id": 0x745,
            "identity": {"id_protocol": "UDS"},
            "iocontrol": {"6900": {"label": "Mirrors", "on": "2F690003", "off": "2F690000"}},
            "routines": {"12A1": {"label": "Self test"}},
        }
    }
}


class _Stdin(io.StringIO):
    def __init__(self, text: str, tty: bool):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    async def fake_execute(terminal, pids, ecu, rid, **kw):
        calls.append({"ecu": ecu, "rid": rid, **kw})

    monkeypatch.setattr(actuators, "mode_routines_execute", fake_execute)
    return calls


def _args(sf: str, **kw) -> argparse.Namespace:
    base = {"routines": "BCM", "rid": "12A1", "sf": sf, "yes": False, "verbose": False}
    return argparse.Namespace(**{**base, "json": False, **kw})


def _handle(args):
    asyncio.run(actuators.handle_routines(args, None, DATA, None))  # type: ignore[arg-type]


def test_the_routines_command_has_a_yes_flag():
    args = build_parser().parse_args(["routines", "BCM", "--rid", "12A1", "--sf", "start", "-y"])
    assert args.yes is True


def test_start_declined_exits_1_and_sends_nothing(monkeypatch, capsys, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("n\n", tty=True))
    with pytest.raises(SystemExit) as exc:
        _handle(_args("start"))
    assert exc.value.code == 1
    assert sent == []
    err = capsys.readouterr().err
    assert "warning: --sf start sends startRoutine (SF 0x01) to BCM RID 12A1" in err
    assert "Aborted — nothing changed." in err


def test_start_accepted_sends(monkeypatch, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("y\n", tty=True))
    _handle(_args("start"))
    assert len(sent) == 1


def test_start_with_yes_skips_the_question(monkeypatch, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("", tty=False))
    _handle(_args("start", yes=True))
    assert len(sent) == 1


def test_start_without_a_terminal_and_without_yes_is_refused(monkeypatch, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("y\n", tty=False))
    with pytest.raises(PromptUnavailable, match="--yes"):
        _handle(_args("start"))
    assert sent == []


def test_results_does_not_ask(monkeypatch, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("", tty=False))
    _handle(_args("results"))
    assert len(sent) == 1


def test_a_kwp2000_ecu_is_refused_before_the_start_question(monkeypatch, capsys, sent):
    data = {
        "ecus": {
            "BMS": {
                "tx_id": 0x7E4,
                "identity": {"id_protocol": "KWP2000"},
                "routines": {"05": {}},
            }
        }
    }
    monkeypatch.setattr("sys.stdin", _Stdin("y\n", tty=True))
    args = _args("start", routines="BMS", rid="05")
    with pytest.raises(SafetyBlocked, match="BMS speaks KWP2000"):
        asyncio.run(actuators.handle_routines(args, None, data, None))  # type: ignore[arg-type]
    assert sent == []
    assert "startRoutine (SF 0x01)" not in capsys.readouterr().err


def test_a_bad_sf_is_a_usage_error(capsys, sent):
    with pytest.raises(SystemExit) as exc:
        _handle(_args("explode"))
    assert exc.value.code == 2
    assert capsys.readouterr().err.startswith("error: --sf must be one of: results, start, stop")
    assert sent == []


def test_io_json_of_an_unknown_ecu_fails_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr("canair.ecus.pids.load_pids", lambda *a, **k: DATA)
    rc = io_cmd.run(argparse.Namespace(iocontrol="NOPE", did=None, json=True))
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: no IOControl DIDs for ECU: NOPE" in captured.err


def test_io_without_an_ecu_is_a_usage_error_on_stderr(capsys):
    rc = io_cmd.run(argparse.Namespace(iocontrol=None, did=None, json=False))
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: specify an ECU")


UNKNOWN_DATA = {"ecus": {"BCM": {"tx_id": 0x745, "routines": {"12A1": {"label": "Self test"}}}}}


def _handle_unknown(args):
    asyncio.run(actuators.handle_routines(args, None, UNKNOWN_DATA, None))  # type: ignore[arg-type]


def test_an_ecu_of_unknown_protocol_is_asked_about_even_for_results(monkeypatch, capsys, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("n\n", tty=True))
    with pytest.raises(SystemExit) as exc:
        _handle_unknown(_args("results"))
    assert exc.value.code == 1
    assert sent == []
    err = capsys.readouterr().err
    assert "the profile records no protocol for BCM" in err
    assert "31 03 12A1 is UDS RoutineControl" in err
    assert "hint: run `canair identity BCM` first" in err


def test_an_ecu_of_unknown_protocol_without_a_terminal_needs_yes(monkeypatch, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("", tty=False))
    with pytest.raises(PromptUnavailable, match="--yes"):
        _handle_unknown(_args("results"))
    assert sent == []


def test_yes_answers_the_unknown_protocol_question(monkeypatch, sent):
    monkeypatch.setattr("sys.stdin", _Stdin("", tty=False))
    _handle_unknown(_args("results", yes=True))
    assert len(sent) == 1
