"""``canair reset``'s live run: one granted ECUReset, then TesterPresent until the ECU answers."""

from __future__ import annotations

import asyncio
import json

import pytest

from canair.live.actuate import ecu_reset
from canair.live.actuate.ecu_reset import mode_ecu_reset
from canair.safety.blocklist import SafetyBlocked, SessionPolicy, check_command_safety
from canair.uds.protocol import KWP, UDS
from canair.uds.subfunctions import EcuResetType
from tests.support.fakes import NO_DATA, FakeTerminal, nrc, ok

UDS_ECU, KWP_ECU = 0x7E4, 0x7E2


class GuardedTerminal(FakeTerminal):
    """Refuses what the blocklist refuses under the policy in force, as the real terminals do.

    ``silent_polls`` TesterPresents go unanswered before the ECU answers again.
    """

    def __init__(self, reset_reply: dict, silent_polls: int = 2, **kw):
        super().__init__(**kw)
        self.session_policy = SessionPolicy({UDS_ECU: UDS, KWP_ECU: KWP})
        self.reset_reply = reset_reply
        self.silent_polls = silent_polls

    async def send_uds(self, req, timeout=None, expected_sid=None, expected_echo=None, **kw):
        blocked = check_command_safety(req, self.session_policy, self.header)
        if blocked:
            raise SafetyBlocked(blocked)
        await super().send_uds(req, timeout, expected_sid, expected_echo, **kw)
        if req.startswith("11"):
            return dict(self.reset_reply)
        if req == "3E00":
            if self.silent_polls > 0:
                self.silent_polls -= 1
                return dict(NO_DATA)
            return ok("7E00")
        return ok("50" + req[2:])


@pytest.fixture(autouse=True)
def _no_waits(monkeypatch):
    monkeypatch.setattr(ecu_reset, "_POLL_GAP_S", 0)
    monkeypatch.setattr(ecu_reset, "_FIRST_POLL_DELAY_S", 0)


def _run(term, reset_type=EcuResetType.HARD_RESET, tx_id=UDS_ECU, **kw):
    kw.setdefault("ecu", "BMS (0x7E4)")
    return asyncio.run(mode_ecu_reset(term, tx_id, reset_type, **kw))


def test_an_accepted_reset_waits_until_the_ecu_answers_again(capsys):
    term = GuardedTerminal(ok("5101"))
    assert _run(term) is True
    assert term.requests == ["1101", "3E00", "3E00", "3E00"]
    assert term.uds_kwargs[0]["expected_sid"] == 0x11
    assert term.uds_kwargs[0]["expected_echo"] == bytes([0x01])
    out = capsys.readouterr()
    assert "Command: 1101 (ECUReset hardReset, UDS)" in out.out
    assert "accepted the reset: 5101" in out.out
    assert "answered again" in out.out and "never went silent" not in out.out
    assert "canair dtc --all" in out.err


def test_the_grant_ends_with_the_send():
    term = GuardedTerminal(ok("5103"))
    _run(term, EcuResetType.SOFT_RESET)
    assert term.session_policy.grants == frozenset()
    assert check_command_safety("1103", term.session_policy, UDS_ECU) is not None


def test_an_answer_with_no_silence_says_the_restart_was_not_seen(capsys):
    term = GuardedTerminal(ok("5101"), silent_polls=0)
    assert _run(term) is True
    assert "(it never went silent)" in capsys.readouterr().out


def test_a_refusal_sends_no_testerpresent_and_hints(capsys):
    term = GuardedTerminal(nrc(0x22, "conditionsNotCorrect"))
    assert _run(term) is False
    assert term.requests == ["1101"]
    err = capsys.readouterr().err
    assert "error: BMS (0x7E4) refused the reset: NRC 0x22 (conditionsNotCorrect)" in err
    assert "drive system off" in err


def test_no_reply_still_waits_and_does_not_claim_success(capsys):
    term = GuardedTerminal(dict(NO_DATA), silent_polls=1)
    assert _run(term) is False
    assert term.requests == ["1101", "3E00", "3E00"]
    err = capsys.readouterr().err
    assert "may have restarted before it replied" in err


def test_an_ecu_that_never_answers_is_reported(capsys):
    term = GuardedTerminal(ok("5101"), silent_polls=10**6)
    assert _run(term, restart_timeout=0.05) is False
    assert "did not answer within 0.05 s" in capsys.readouterr().err


def test_restart_timeout_zero_does_not_wait():
    term = GuardedTerminal(ok("5101"))
    assert _run(term, restart_timeout=0) is True
    assert term.requests == ["1101"]


def test_session_opens_first():
    term = GuardedTerminal(ok("5101"))
    _run(term, session=True, restart_timeout=0)
    assert term.sent == ["1003", "1101"]


def test_a_refused_type_sends_nothing():
    term = GuardedTerminal(ok("5103"))
    with pytest.raises(SafetyBlocked, match="speaks KWP2000"):
        _run(term, EcuResetType.SOFT_RESET, tx_id=KWP_ECU)
    assert term.sent == []


def test_json_stdout_is_one_document(capsys):
    term = GuardedTerminal(ok("5101"), silent_polls=1)
    _run(term, as_json=True)
    out = capsys.readouterr()
    doc = json.loads(out.out)
    assert doc["accepted"] is True
    assert doc["command"] == "1101"
    assert doc["reset_type"] == "hardReset"
    assert doc["restart"]["went_silent"] is True
    assert doc["restart"]["back_after_s"] is not None
    assert "Command: 1101" in out.err
