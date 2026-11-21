"""``canair routines ECU --rid RID``: a failed lookup is an error; ``--json`` keeps stdout clean."""

from __future__ import annotations

import asyncio
import json

import pytest

from canair.live.actuate.routines_execute import check_routine_request_size, mode_routines_execute
from canair.live.actuate.routines_list import mode_routines_list
from canair.safety.blocklist import SessionPolicy
from canair.uds.parse import parse_uds_response
from canair.uds.protocol import UDS
from canair.uds.services import ROUTINE_CONTROL
from canair.uds.subfunctions import RoutineControlType
from tests.support.fakes import NO_DATA, ok
from tests.support.fakes import FakeTerminal as SharedFakeTerminal

DATA = {
    "ecus": {
        "BCM": {
            "tx_id": 0x745,
            "identity": {"id_protocol": "UDS"},
            "routines": {"12A1": {"label": "Self test"}},
        }
    }
}


class FakeTerminal(SharedFakeTerminal):
    """Answers every request positively."""

    def __init__(self):
        super().__init__()
        self.session_policy = SessionPolicy.from_profile(DATA)

    async def send_uds(self, cmd: str, timeout: float | None = 3.0, **kwargs) -> dict:
        await super().send_uds(cmd, timeout, **kwargs)
        if len(cmd) == 4 and cmd.startswith("10"):
            return {"ok": True, "hex": "50" + cmd[2:]}
        # A response echoes the type and RID, not the option record. Its own status follows.
        reply = "71" + cmd[2:8] + "EE"
        return {"ok": True, "hex": reply, "bytes": bytes.fromhex(reply)}


def _run(ecu: str, rid: str, *, terminal: FakeTerminal | None = None, **kw):
    term = terminal or FakeTerminal()
    asyncio.run(mode_routines_execute(term, DATA, ecu, rid, **kw))  # type: ignore[arg-type]
    return term


def test_unknown_ecu_is_an_error_with_exit_1(capsys):
    with pytest.raises(SystemExit) as exc:
        _run("NOPE", "12A1")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: no routines for ECU: NOPE" in captured.err
    assert "hint: ECUs with routines: BCM" in captured.err


def test_unknown_rid_is_an_error_with_exit_1(capsys):
    with pytest.raises(SystemExit) as exc:
        _run("BCM", "FFFF")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: unknown RID FFFF for BCM" in captured.err
    assert "hint: available RIDs: 12A1" in captured.err


def test_json_stdout_is_only_the_document(capsys):
    term = _run("BCM", "12A1", as_json=True)
    assert len(term.requests) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["rid"] == "12A1"
    assert "Command:" in captured.err and "Positive response" in captured.err


def test_without_json_the_report_is_on_stdout(capsys):
    _run("BCM", "12A1")
    captured = capsys.readouterr()
    assert "Positive response" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(("reply", "expected"), [(ok("710312A1EE"), True), (NO_DATA, False)])
def test_mode_returns_whether_the_routine_succeeded(reply, expected):
    term = SharedFakeTerminal(responses={"310312A1": reply})
    term.session_policy = SessionPolicy.from_profile(DATA)
    result = asyncio.run(mode_routines_execute(term, DATA, "BCM", "12A1"))
    assert result is expected


@pytest.mark.parametrize(
    ("sub_function", "request_hex", "echo"),
    [
        (RoutineControlType.START_ROUTINE, "310112A100FF", "0112A1"),
        (RoutineControlType.STOP_ROUTINE, "310212A100FF", "0212A1"),
        (RoutineControlType.REQUEST_ROUTINE_RESULTS, "310312A100FF", "0312A1"),
    ],
)
def test_option_record_is_sent_and_the_reply_echo_excludes_it(
    sub_function, request_hex, echo, capsys
):
    term = _run("BCM", "12A1", sub_function=sub_function, option_record=b"\x00\xff", as_json=True)
    assert term.requests == [request_hex]
    sent = term.uds_kwargs[-1]
    assert sent["expected_sid"] == ROUTINE_CONTROL
    assert sent["expected_echo"] == bytes.fromhex(echo)
    assert sent["retries"] == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == request_hex
    assert output["response"] == "71" + echo + "EE"


def test_a_stale_routine_reply_is_not_accepted_as_the_record_response():
    request = "310112A100FF"
    kwargs = {"expected_sid": ROUTINE_CONTROL, "expected_echo": bytes.fromhex("0112A1")}
    assert parse_uds_response("710112A1EE", protocol=UDS, request=request, **kwargs)["ok"]
    stale = parse_uds_response("710112A2EE", protocol=UDS, request=request, **kwargs)
    assert not stale["ok"] and stale["error_kind"] == "stale"


def test_routine_header_and_request_share_one_transaction():
    term = _run("BCM", "12A1", option_record=b"\xaa")
    i = term.calls.index(("send_uds", "310312A1AA"))
    assert term.calls[i - 2 : i + 2] == [
        ("transaction_enter", None),
        ("set_header", 0x745),
        ("send_uds", "310312A1AA"),
        ("transaction_exit", None),
    ]


def test_direct_routine_timeout_is_forwarded():
    term = _run("BCM", "12A1", option_record=b"\xaa", timeout=8.5)
    assert term.uds_kwargs[-1]["timeout"] == 8.5


@pytest.mark.parametrize("limit", [7, 0xFFF])
def test_request_size_counts_the_service_type_and_rid(limit):
    check_routine_request_size(b"\xaa" * (limit - 4), limit)
    with pytest.raises(ValueError, match=f"request is {limit + 1} bytes.*at most {limit}"):
        check_routine_request_size(b"\xaa" * (limit - 3), limit)


def test_oversized_request_is_refused_before_the_session_or_header():
    term = FakeTerminal()
    term.max_request_bytes = 7
    with pytest.raises(ValueError, match=r"request is 8 bytes.*at most 7"):
        _run("BCM", "12A1", terminal=term, option_record=b"\x00" * 4)
    assert term.sent == []
    assert term.headers == []


def test_long_record_is_sent_on_a_segmenting_transport():
    term = _run("BCM", "12A1", option_record=bytes.fromhex("0102030405"))
    assert term.requests == ["310312A10102030405"]


def test_the_list_of_an_unknown_ecu_is_an_error_on_stderr(capsys):
    assert mode_routines_list(DATA, "NOPE", as_json=True) is False
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: no routines defined for ECU: NOPE" in captured.err
