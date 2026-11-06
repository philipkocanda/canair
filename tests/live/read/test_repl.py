"""The multi-ECU REPL (``live/read/repl.py``): its commands, and the keepalive beside them."""

from __future__ import annotations

import asyncio
import os
import signal

import pytest

from canair.cli.profile_context import active
from canair.live.read import repl
from canair.live.read.repl import ReplCommands
from canair.transport.session_manager import SessionManager
from tests.support.fakes import FakeTerminal, ok

INDEX = {"BMS": {"tx_id": 0x7E4, "pids": {}}, "IGPM": {"tx_id": 0x770, "pids": {}}}


def _commands(term: FakeTerminal) -> ReplCommands:
    return ReplCommands(SessionManager(term), INDEX, {"ecus": {}}, False, False, active())


@pytest.mark.asyncio
async def test_a_typed_request_goes_to_the_ecu_its_atsh_named():
    term = FakeTerminal({"2101": ok("6101AA")}, serialize=True)
    cmds = _commands(term)
    await cmds.handle("ATSH7E4")
    await cmds.sm.send_keepalive(0x770)  # a keepalive moved the header meanwhile
    await cmds.handle("2101")
    assert term.addressed[-1] == (0x7E4, "2101")


@pytest.mark.asyncio
async def test_session_opens_and_is_listed(capsys):
    term = FakeTerminal()
    cmds = _commands(term)
    assert await cmds.handle("session BMS") is True
    await cmds.handle("!sessions")
    out = capsys.readouterr().out
    assert "Session opened on 0x7E4" in out and "0x7E4 (BMS)" in out
    assert term.sessions == ["03"]


@pytest.mark.asyncio
async def test_an_unknown_ecu_and_skm_off_elm327_are_errors(capsys):
    cmds = _commands(FakeTerminal())
    await cmds.handle("session NOPE")
    await cmds.handle("skm acc")
    err = capsys.readouterr().err
    assert "error: unknown ECU: NOPE" in err
    assert "error: skm needs an ELM327 transport" in err


@pytest.mark.asyncio
async def test_quit_ends_it():
    assert await _commands(FakeTerminal()).handle("quit") is False


def test_the_repl_restores_the_sigint_handler_and_stops_the_keepalive(monkeypatch):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"quit\n")
    os.close(write_fd)
    stdin = os.fdopen(read_fd, "rb")
    monkeypatch.setattr(repl.sys, "stdin", stdin)
    before = signal.getsignal(signal.SIGINT)
    sm = SessionManager(FakeTerminal())

    async def run():
        await repl._multi_repl(sm, INDEX, {"ecus": {}}, False, profile=active())

    try:
        asyncio.run(run())
    finally:
        stdin.close()
    assert signal.getsignal(signal.SIGINT) is before
    assert not sm.keepalive_running
