"""One-shot routine outcomes determine the CLI exit status, with or without JSON."""

from __future__ import annotations

import asyncio
import json

import pytest

from canair.cli import build_parser, main
from canair.cli.commands import routines
from canair.cli.live.handlers import actuators
from canair.cli.profile_context import active
from canair.live.actuate import routines_execute
from canair.live.session import SessionOptions, run_session_guarded
from canair.safety.blocklist import SessionPolicy
from canair.transport.session_manager import SessionManager
from tests.support.fakes import NO_DATA, FakeTerminal, nrc, ok

DATA = {
    "ecus": {
        "BCM": {
            "tx_id": 0x745,
            "identity": {"id_protocol": "UDS"},
            "routines": {"12A1": {"label": "Self test"}},
        }
    }
}


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("with_options", [False, True])
@pytest.mark.parametrize(
    ("sf", "sub", "reply", "detail"),
    [
        ("start", "01", nrc(0x22, "conditionsNotCorrect"), "NRC 0x22: conditionsNotCorrect"),
        ("stop", "02", NO_DATA, "NO DATA"),
        ("results", "03", {"ok": False, "raw": "", "error": "timeout"}, "timeout"),
        ("results", "03", {"ok": False, "raw": "", "error": "Echo mismatch"}, "Echo mismatch"),
        ("results", "03", {"ok": False, "raw": "", "error": "truncated ISO-TP"}, "truncated"),
        ("results", "03", {"ok": False, "raw": ""}, "unknown error"),
    ],
)
def test_unsuccessful_response_exits_one_and_reports_on_stderr(
    sf, sub, reply, detail, as_json, with_options, capsys
):
    argv = ["routines", "BCM", "--rid", "12A1", "--sf", sf, "--yes"]
    if with_options:
        argv.extend(["--option-record", "AA"])
    if as_json:
        argv.append("--json")
    args = build_parser().parse_args(argv)
    request_hex = f"31{sub}12A1" + ("AA" if with_options else "")
    term = FakeTerminal(responses={request_hex: reply})
    term.session_policy = SessionPolicy.from_profile(DATA)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(actuators.handle_routines(args, term, DATA, None))
    assert exc.value.code == 1
    assert term.requests == [request_hex]
    out = capsys.readouterr()
    assert f"error: BCM routine 12A1: {detail}" in out.err
    if as_json:
        result = json.loads(out.out)
        assert result["ok"] is False and result["command"] == request_hex
        assert result["nrc"] == ("0x22" if reply.get("nrc") == 0x22 else None)
    else:
        assert "Command:" in out.out and detail not in out.out


@pytest.mark.parametrize(("reply", "expected"), [(ok("710312A1EE"), 0), (NO_DATA, 1)])
def test_main_propagates_outcome_and_cleans_up_the_held_session(
    reply, expected, monkeypatch, capsys
):
    term = FakeTerminal(responses={"310312A1": reply})
    term.session_policy = SessionPolicy.from_profile(DATA)
    sessions = []

    class TrackingSessions(SessionManager):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)

    monkeypatch.setattr(routines_execute, "SessionManager", TrackingSessions)

    def fake_run_live(args, operation):
        async def run_operation(terminal, data):
            await operation.handler(args, terminal, data, None)

        return asyncio.run(
            run_session_guarded(
                term,
                DATA,
                "in-memory",
                run_operation,
                options=SessionOptions(as_json=True, learn_frames=False),
                transport_label="fake",
                profile=active(),
            )
        )

    monkeypatch.setattr(routines, "run_live", fake_run_live)
    try:
        code = main(["routines", "BCM", "--rid", "12A1", "--json"])
    except SystemExit as exc:
        code = exc.code
    assert code == expected
    out = capsys.readouterr()
    assert json.loads(out.out)["ok"] is (expected == 0)
    assert ("error: BCM routine 12A1: NO DATA" in out.err) is (expected == 1)
    assert len(sessions) == 1
    assert not sessions[0].keepalive_running and sessions[0].active_sessions == []
