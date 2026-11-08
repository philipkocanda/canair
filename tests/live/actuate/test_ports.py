"""SharedSessions: open or switch a session on a manager somebody else keeps alive."""

from __future__ import annotations

import asyncio

from canair.live.actuate.ports import SharedSessions
from canair.transport.session_manager import SessionManager
from tests.support.fakes import FakeTerminal


def _shared() -> tuple[SharedSessions, SessionManager, FakeTerminal]:
    terminal = FakeTerminal()
    sm = SessionManager(terminal)
    return SharedSessions(lambda: sm), sm, terminal


def test_no_session_opens_the_default_one():
    sessions, sm, terminal = _shared()
    assert asyncio.run(sessions.ensure(0x770, None)) is True
    assert terminal.sessions == ["03"]
    assert sm.session_mode(0x770) == 0x03


def test_an_open_session_of_the_mode_is_reused():
    sessions, sm, terminal = _shared()
    asyncio.run(sm.open_session(0x770, mode="03"))
    assert asyncio.run(sessions.ensure(0x770, "03")) is True
    assert asyncio.run(sessions.ensure(0x770, None)) is True
    assert terminal.sessions == ["03"]  # the one open_session above


def test_another_mode_switches_the_session():
    sessions, sm, terminal = _shared()
    asyncio.run(sm.open_session(0x770, mode="03"))
    asyncio.run(sessions.ensure(0x770, "C0"))
    assert terminal.sessions[-1] == "C0"
    assert sm.session_mode(0x770) == 0xC0


def test_it_never_starts_a_keepalive():
    sessions, sm, _ = _shared()
    asyncio.run(sessions.ensure(0x770, "03"))
    assert not sm.keepalive_running
