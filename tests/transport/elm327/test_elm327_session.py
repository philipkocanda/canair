"""Pins what the ELM327 session ritual (``transport/elm327/session.py``) sends today.

``enter_extended_session`` is one of the copies ``plans/2026-11-05-one-session-owner.md``
replaces with ``SessionManager``; these tests record its requests, their order, timeouts and
validation, so the change shows up as an edit here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from canair.transport.elm327 import session as elm327_session
from tests.support.fakes import NO_DATA, ok


def _terminal(replies: dict[str, list]):
    calls: list[tuple] = []

    async def send_uds(request, **kwargs):
        calls.append(("send_uds", request, kwargs))
        queue = replies.get(request, [])
        return queue.pop(0) if queue else NO_DATA

    async def send_command(cmd, timeout=None):
        calls.append(("send_command", cmd))
        return "OK"

    term = SimpleNamespace(
        send_uds=send_uds,
        send_command=send_command,
        elm_timeout_cmd="ATST96",
        verbose=False,
        _cur_header=0x7A5,
        diag=SimpleNamespace(transport="fake"),
    )
    return term, calls


async def _enter(term, **kwargs):
    ok_, task = await elm327_session.enter_extended_session(term, **kwargs)
    assert task is not None
    task.cancel()
    return ok_


class TestElmSessionRitual:
    @pytest.mark.asyncio
    async def test_the_session_request_is_sid_validated(self):
        term, calls = _terminal({"1003": [ok("5003")]})
        assert await _enter(term) is True
        assert calls == [("send_uds", "1003", {"timeout": 5.0, "expected_sid": 0x10})]

    @pytest.mark.asyncio
    async def test_a_non_answer_is_retried_once(self, monkeypatch):
        async def no_sleep(_s):
            return None

        monkeypatch.setattr(elm327_session.asyncio, "sleep", no_sleep)
        term, calls = _terminal({"1081": [NO_DATA, ok("5081")]})
        assert await _enter(term, mode="81") is True
        assert [c[1] for c in calls] == ["1081", "1081"]

    @pytest.mark.asyncio
    async def test_wake_shortens_the_adapter_wait_and_restores_it(self):
        term, calls = _terminal({"1001": [ok("5001")], "1003": [ok("5003")]})
        await _enter(term, wake=True)
        assert [c[1] for c in calls] == ["ATST10", "1001", "ATST96", "1003"]
        assert calls[1][2] == {"timeout": 3.0, "expected_sid": 0x10}
