"""RoutineControl option records: parser, handler, safeguards and the ELM wire, all offline."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from canair.cli import build_parser
from canair.cli.commands import routines
from canair.cli.live.handlers import actuators
from canair.core.prompt import PromptUnavailable
from canair.safety.blocklist import SafetyBlocked, SessionPolicy
from canair.transport.elm327.terminal import Elm327Terminal
from tests.support.fakes import FakeTerminal, QueuedChannel, ok

DATA = {
    "ecus": {
        "BCM": {
            "tx_id": 0x745,
            "identity": {"id_protocol": "UDS"},
            "routines": {"12A1": {"label": "Self test"}},
        }
    }
}


def _args(*args: str):
    return build_parser().parse_args(["routines", "BCM", *args])


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize("text", ["AABB", "aabb", "AA BB", " AA  BB "])
def test_parser_accepts_whole_hex_bytes(text):
    args = _args("--rid", "12A1", "--option-record", text)
    assert args.option_record == b"\xaa\xbb"


@pytest.mark.parametrize("text", ["", " ", "A", "AAB", "GG", "0xAA", "AA:BB"])
def test_parser_rejects_empty_odd_or_non_hex_records(text, capsys):
    with pytest.raises(SystemExit) as exc:
        _args("--rid", "12A1", "--option-record", text)
    assert exc.value.code == 2
    assert "--option-record: must be non-empty whole hex bytes" in capsys.readouterr().err


def test_omitted_record_is_distinct_from_an_empty_argument():
    assert _args("--rid", "12A1").option_record is None


def test_option_record_needs_a_value(capsys):
    with pytest.raises(SystemExit) as exc:
        _args("--rid", "12A1", "--option-record")
    assert exc.value.code == 2
    assert "--option-record: expected one argument" in capsys.readouterr().err


@pytest.mark.parametrize("json_flag", [False, True])
def test_record_requires_rid_even_for_a_tui_or_json_listing(json_flag, monkeypatch, capsys):
    def no_device(*_args):
        pytest.fail("invalid arguments must not start a live session")

    monkeypatch.setattr(routines, "run_live", no_device)
    args = _args("--option-record", "AA", *(["--json"] if json_flag else []))
    assert routines.run(args) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "error: --option-record requires --rid" in out.err


@pytest.mark.parametrize(
    ("sf", "request_hex", "response", "echo"),
    [
        ("start", "310112A100FF", "710112A1EE", "0112A1"),
        ("stop", "310212A100FF", "710212A1EE", "0212A1"),
        ("results", "310312A100FF", "710312A1EE", "0312A1"),
    ],
)
def test_cli_dispatches_option_record_through_the_live_handler(
    sf, request_hex, response, echo, capsys
):
    args = _args("--rid", "12A1", "--sf", sf, "--option-record", "00 FF", "--yes", "--json")
    term = FakeTerminal(responses={request_hex: ok(response)})
    term.session_policy = SessionPolicy.from_profile(DATA)
    asyncio.run(actuators.handle_routines(args, term, DATA, None))
    assert term.requests == [request_hex]
    assert term.uds_kwargs[-1]["expected_echo"] == bytes.fromhex(echo)
    result = json.loads(capsys.readouterr().out)
    assert result["command"] == request_hex and result["response"] == response


@pytest.mark.parametrize(("timeout_args", "expected"), [([], 3.0), (["--timeout", "8.5"], 8.5)])
@pytest.mark.parametrize("option_args", [[], ["--option-record", "AA"]])
@pytest.mark.parametrize(("sf", "sub"), [("start", "01"), ("stop", "02"), ("results", "03")])
def test_one_shot_timeout_is_forwarded_without_changing_the_default(
    timeout_args, expected, option_args, sf, sub, capsys
):
    args = _args("--rid", "12A1", "--sf", sf, "--yes", "--json", *option_args, *timeout_args)
    request_hex = f"31{sub}12A1" + ("AA" if option_args else "")
    term = FakeTerminal(responses={request_hex: ok(f"71{sub}12A1EE")})
    term.session_policy = SessionPolicy.from_profile(DATA)
    asyncio.run(actuators.handle_routines(args, term, DATA, None))
    assert term.uds_kwargs[-1]["timeout"] == expected
    assert json.loads(capsys.readouterr().out)["ok"]


def test_start_confirmation_names_the_option_record_and_declining_sends_nothing(
    monkeypatch, capsys
):
    args = _args("--rid", "12A1", "--sf", "start", "--option-record", "00ff")
    term = FakeTerminal()
    monkeypatch.setattr("sys.stdin", _Tty("n\n"))
    with pytest.raises(SystemExit) as exc:
        asyncio.run(actuators.handle_routines(args, term, DATA, None))
    assert exc.value.code == 1
    assert term.sent == []
    assert "RID 12A1 with option record 00FF" in capsys.readouterr().err


def test_start_without_a_terminal_or_yes_is_still_refused(monkeypatch):
    args = _args("--rid", "12A1", "--sf", "start", "--option-record", "AA")
    term = FakeTerminal()
    monkeypatch.setattr("sys.stdin", io.StringIO())
    with pytest.raises(PromptUnavailable, match="--yes"):
        asyncio.run(actuators.handle_routines(args, term, DATA, None))
    assert term.sent == []


def test_unknown_protocol_warning_includes_the_complete_request(monkeypatch, capsys):
    unknown = {"ecus": {"BCM": {"tx_id": 0x745, "routines": {"12A1": {}}}}}
    args = _args("--rid", "12A1", "--option-record", "00FF")
    term = FakeTerminal()
    monkeypatch.setattr("sys.stdin", _Tty("n\n"))
    with pytest.raises(SystemExit) as exc:
        asyncio.run(actuators.handle_routines(args, term, unknown, None))
    assert exc.value.code == 1 and term.sent == []
    assert "31 03 12A1 00FF is UDS RoutineControl" in capsys.readouterr().err


def test_yes_accepts_an_unknown_protocol_without_assuming_a_uds_echo(capsys):
    unknown = {"ecus": {"BCM": {"tx_id": 0x745, "routines": {"12A1": {}}}}}
    args = _args("--rid", "12A1", "--option-record", "AA", "--yes", "--json")
    term = FakeTerminal(responses={"310312A1AA": ok("710312A1EE")})
    term.session_policy = SessionPolicy.from_profile(unknown)
    asyncio.run(actuators.handle_routines(args, term, unknown, None))
    assert term.requests == ["310312A1AA"]
    assert term.uds_kwargs[-1]["expected_echo"] is None
    assert json.loads(capsys.readouterr().out)["ok"]


def test_an_oversized_elm_request_is_refused_before_any_question_or_send(monkeypatch, capsys):
    channel = QueuedChannel()
    term = Elm327Terminal(channel)
    term.session_policy = SessionPolicy.from_profile(DATA)
    args = _args("--rid", "12A1", "--sf", "start", "--option-record", "AABBCCDD", "--json")
    monkeypatch.setattr(actuators, "confirm", lambda *a, **k: pytest.fail("must refuse first"))
    with pytest.raises(SystemExit) as exc:
        asyncio.run(actuators.handle_routines(args, term, DATA, None))
    assert exc.value.code == 2 and channel.sent == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "request is 8 bytes; this transport allows at most 7 bytes" in captured.err


@pytest.mark.parametrize("transport", ["wican-ws", "elm327-tcp"])
def test_elm_sends_a_three_byte_record_at_its_limit(transport, monkeypatch, capsys):
    channel = QueuedChannel("OK\r>", "OK\r>", "5003\r>", "710112A1EE\r>")
    channel.transport_name = transport
    term = Elm327Terminal(channel, expected_responses=False)
    term.session_policy = SessionPolicy.from_profile(DATA)
    send = term.send_uds
    routine_timeouts = []

    async def record_send(cmd, *a, **kw):
        if cmd.startswith("31"):
            routine_timeouts.append(kw.get("timeout"))
        return await send(cmd, *a, **kw)

    monkeypatch.setattr(term, "send_uds", record_send)
    args = _args(
        "--rid",
        "12A1",
        "--sf",
        "start",
        "--option-record",
        "AABBCC",
        "--timeout",
        "8.5",
        "--yes",
        "--json",
    )
    asyncio.run(actuators.handle_routines(args, term, DATA, None))
    assert "310112A1AABBCC\r" in channel.sent
    assert routine_timeouts == [8.5]
    result = json.loads(capsys.readouterr().out)
    assert result["command"] == "310112A1AABBCC" and result["response"] == "710112A1EE"


@pytest.mark.parametrize(
    ("reply", "nrc"), [("7F3121\r>", "0x21"), ("7F3122\r>", "0x22"), ("NO DATA\r>", None)]
)
def test_an_elm_nrc_or_non_answer_is_reported_without_retrying(reply, nrc, capsys):
    channel = QueuedChannel("OK\r>", "OK\r>", "5003\r>", reply)
    term = Elm327Terminal(channel, expected_responses=False)
    term.session_policy = SessionPolicy.from_profile(DATA)
    args = _args("--rid", "12A1", "--sf", "start", "--option-record", "AA", "--yes", "--json")
    with pytest.raises(SystemExit) as exc:
        asyncio.run(actuators.handle_routines(args, term, DATA, None))
    assert exc.value.code == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert not result["ok"] and result["nrc"] == nrc
    assert "error: BCM routine 12A1:" in captured.err
    assert channel.sent.count("310112A1AA\r") == 1


def test_an_elm_stale_rid_is_rejected_even_with_an_option_record(capsys):
    channel = QueuedChannel("OK\r>", "OK\r>", "5003\r>", "710112A2EE\r>")
    channel.after_drain = ["ELM327 v2.3\r>"]  # the resync probe
    term = Elm327Terminal(channel, expected_responses=False)
    term.session_policy = SessionPolicy.from_profile(DATA)
    args = _args("--rid", "12A1", "--sf", "start", "--option-record", "AA", "--yes", "--json")
    with pytest.raises(SystemExit) as exc:
        asyncio.run(actuators.handle_routines(args, term, DATA, None))
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert not json.loads(captured.out)["ok"]
    assert "Echo mismatch" in captured.err
    assert channel.sent.count("310112A1AA\r") == 1


@pytest.mark.parametrize(("rid", "unsafe"), [("E200", False), ("E200", True), ("FF00", False)])
@pytest.mark.parametrize("sf", ["start", "stop"])
def test_option_record_does_not_bypass_the_real_transport_blocklist(rid, unsafe, sf):
    data = {
        "ecus": {
            "BCM": {
                "tx_id": 0x745,
                "identity": {"id_protocol": "UDS"},
                "routines": {rid: {"label": "Blocked routine"}},
            }
        }
    }
    channel = QueuedChannel("OK\r>", "OK\r>", "5003\r>")
    term = Elm327Terminal(channel, unsafe=unsafe, expected_responses=False)
    term.session_policy = SessionPolicy.from_profile(data)
    args = _args("--rid", rid, "--sf", sf, "--option-record", "AABB", "--yes")
    with pytest.raises(SafetyBlocked, match="BLOCKED"):
        asyncio.run(actuators.handle_routines(args, term, data, None))
    assert not any(request.startswith("31") for request in channel.sent)


def test_reading_blocked_routine_results_remains_allowed_with_options(capsys):
    data = {
        "ecus": {
            "BCM": {
                "tx_id": 0x745,
                "identity": {"id_protocol": "UDS"},
                "routines": {"FF00": {}},
            }
        }
    }
    channel = QueuedChannel("OK\r>", "OK\r>", "5003\r>", "7103FF00EE\r>")
    term = Elm327Terminal(channel, expected_responses=False)
    term.session_policy = SessionPolicy.from_profile(data)
    args = _args("--rid", "FF00", "--option-record", "AA", "--json")
    asyncio.run(actuators.handle_routines(args, term, data, None))
    assert "3103FF00AA\r" in channel.sent
    assert json.loads(capsys.readouterr().out)["ok"]
