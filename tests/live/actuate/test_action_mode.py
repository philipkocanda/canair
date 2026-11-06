"""The live action run (canair.live.actuate.action): plan, check, confirm, re-check, send.

Driven against a fake terminal that enforces the command blocklist with whatever
policy it holds, so these prove the grant is what lets ``10 D1`` through and that
it is gone again afterwards.
"""

from __future__ import annotations

import asyncio

import pytest

from canair.actions import ActionError, parse_action
from canair.cli.profile_context import active
from canair.core.prompt import PromptUnavailable
from canair.live.actuate import action as mode
from canair.safety.blocklist import SafetyBlocked, SessionPolicy, check_command_safety
from tests.support.fakes import FakeTerminal

SLEEP = parse_action(
    "sleep",
    {
        "reads": ["IGPM:22BC03"],
        "require": "IGPM.IGN1_SWITCH == 0",
        "requests": [{"to": "functional", "send": "10D1", "response": "none"}],
    },
)
PIDS = {"ecus": {"IGPM": {"tx_id": 0x770, "pids": {}}}, "quirks": ["hk_forced_sleep"]}


class PolicedTerminal(FakeTerminal):
    """A fake that refuses what the blocklist refuses under its current policy."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.policies_seen: list[SessionPolicy] = []

    async def send_uds(self, req, timeout=None, **kw):
        self.policies_seen.append(self.session_policy)
        blocked = check_command_safety(req, self.session_policy, self.header)
        if blocked:
            raise SafetyBlocked(blocked)
        return await super().send_uds(req, timeout=timeout, **kw)


def _values(*rounds):
    """Patch the live reads to return each round of IGPM values in turn."""
    seq = list(rounds)

    async def fake_exec_query(sm, ecu, pids, index, pids_data, verbose, **kw):
        values = seq.pop(0) if len(seq) > 1 else seq[0]
        if values is None:
            return None
        return (f"{ecu} (0x770)", [{"params": [(k, v) for k, v in values.items()]}])

    return fake_exec_query


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(mode, "_REREAD_DELAY_S", 0)
    monkeypatch.setattr("canair.actions.load_actions", lambda *_, **__: {"sleep": SLEEP})

    def _run(terminal, rounds, **kw):
        monkeypatch.setattr("canair.live.read.exec._exec_query", _values(*rounds))
        return asyncio.run(mode.mode_action(terminal, PIDS, "sleep", profile=active(), **kw))

    return _run


def test_sends_the_granted_request_when_the_condition_holds(run):
    t = PolicedTerminal()
    run(t, [{"IGN1_SWITCH": 0}], yes=True)
    assert t.sent == ["10D1"]
    assert t.header == 0x7DF
    # The grant was active for the send and is gone afterwards.
    assert t.policies_seen[0].is_granted("10D1", 0x7DF)
    assert t.session_policy == SessionPolicy()


def test_a_false_condition_sends_nothing(run):
    t = PolicedTerminal()
    with pytest.raises(SafetyBlocked, match="FALSE"):
        run(t, [{"IGN1_SWITCH": 1}], yes=True)
    assert t.sent == []


def test_an_unread_signal_is_reread_once_then_refused(run):
    t = PolicedTerminal()
    with pytest.raises(SafetyBlocked, match="UNKNOWN"):
        run(t, [None, None], yes=True)
    assert t.sent == []


def test_an_unread_signal_that_reads_on_retry_passes(run):
    t = PolicedTerminal()
    run(t, [None, {"IGN1_SWITCH": 0}], yes=True)
    assert t.sent == ["10D1"]


def test_the_condition_is_checked_again_after_confirmation(run):
    t = PolicedTerminal()
    with pytest.raises(SafetyBlocked, match="became FALSE"):
        run(t, [{"IGN1_SWITCH": 0}, {"IGN1_SWITCH": 1}], yes=True)
    assert t.sent == []


def test_dry_run_sends_nothing(run):
    t = PolicedTerminal()
    run(t, [{"IGN1_SWITCH": 0}], dry_run=True)
    assert t.sent == []


def test_without_a_terminal_yes_is_required(run, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    t = PolicedTerminal()
    with pytest.raises(PromptUnavailable, match="--yes"):
        run(t, [{"IGN1_SWITCH": 0}])
    assert t.sent == []


def test_a_wrong_confirmation_exits_1_and_sends_nothing(run, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.readline", lambda: "no\n")
    t = PolicedTerminal()
    with pytest.raises(SystemExit) as raised:
        run(t, [{"IGN1_SWITCH": 0}])
    assert raised.value.code == 1
    assert t.sent == []


def test_the_typed_name_confirms(run, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.readline", lambda: "sleep\n")
    t = PolicedTerminal()
    run(t, [{"IGN1_SWITCH": 0}, {"IGN1_SWITCH": 0}])
    assert t.sent


def test_without_the_quirk_the_action_is_refused_before_any_read(monkeypatch):
    reads: list[str] = []

    async def fake_exec_query(*a, **kw):
        reads.append("read")
        return None

    monkeypatch.setattr("canair.live.read.exec._exec_query", fake_exec_query)
    monkeypatch.setattr("canair.actions.load_actions", lambda *_, **__: {"sleep": SLEEP})
    t = PolicedTerminal()
    with pytest.raises(SafetyBlocked, match="none of the profile's quirks grants it"):
        asyncio.run(
            mode.mode_action(t, {"ecus": PIDS["ecus"]}, "sleep", yes=True, profile=active())
        )
    assert reads == [] and t.sent == []


def test_unsafe_does_not_help(monkeypatch):
    """An action's refusal is not the --unsafe prompt: the terminal's flag is never read."""
    t = PolicedTerminal()
    t.unsafe = True
    planned = mode.plan_requests(SLEEP, {"ecus": PIDS["ecus"]}, profile=active())
    with pytest.raises(SafetyBlocked):
        mode.action_policy(SLEEP, planned, t.session_policy, {"ecus": PIDS["ecus"]})


def test_an_unknown_action(monkeypatch):
    monkeypatch.setattr("canair.actions.load_actions", lambda *_, **__: {})
    with pytest.raises(ActionError, match="no action"):
        asyncio.run(mode.mode_action(PolicedTerminal(), PIDS, "sleep", profile=active()))


def test_a_request_to_an_ecu_resolves_its_tx_id():
    action = parse_action(
        "lamp",
        {
            "reads": ["IGPM:22BC03"],
            "require": "IGPM.IGN1_SWITCH == 0",
            "requests": [{"to": "igpm", "send": "22BC03"}],
        },
    )
    (planned,) = mode.plan_requests(action, PIDS, profile=active())
    assert planned.tx_id == 0x770
