"""Every executor path resolves a step's ECU name through ``pids.ecu_index_key``.

Steps are normally canonicalised by ``normalize_query_steps`` already, so these pin
consistency: an alias in a hand-built step resolves the same everywhere (the raw
poller's copy is in ``tests/modes/test_raw_monitor.py``).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import canlib.ecus
from canlib.modes.monitor import MonitorController
from canlib.modes.multi_exec import _exec_iocontrol, _exec_query, _exec_session
from canlib.query.steps import resolve_tx_id
from canlib.transport.wake import WakePlan
from tests.support.fakes import FakeTerminal, ok

INDEX = {
    "IGPM": {
        "tx_id": 0x770,
        "pids": {"22BC03": {"parameters": {}}},
        "wake": WakePlan("rapid_read", "1001", 10, 50, "03"),
    }
}


@pytest.fixture(autouse=True)
def sjb_is_an_alias_of_igpm(monkeypatch):
    monkeypatch.setattr(
        canlib.ecus,
        "canonical_ecu_name_safe",
        lambda name, *a, **k: "IGPM" if name.upper() == "SJB" else name.upper(),
    )


def _sm():
    sm = MagicMock()
    sm.keepalive_stale = AsyncMock()
    sm.keepalive_seconds = 0.0
    sm.open_session = AsyncMock(return_value=True)
    sm.terminal = FakeTerminal({"22BC03": ok("62BC03AA")})
    return sm


def test_resolve_tx_id():
    assert resolve_tx_id("sjb", INDEX) == 0x770
    assert resolve_tx_id("7A0", INDEX) == 0x7A0


def test_exec_query():
    label, results = asyncio.run(
        _exec_query(_sm(), "sjb", [], INDEX, {}, False, return_results=True, quiet=True)
    )
    assert label == "IGPM (0x770)"
    assert [r["pid"] for r in results] == ["22BC03"]


def test_exec_session_finds_the_wake_plan():
    sm = _sm()
    asyncio.run(_exec_session(sm, "sjb", True, INDEX))
    assert sm.open_session.await_args.kwargs["wake_plan"] == INDEX["IGPM"]["wake"]


def test_exec_iocontrol(monkeypatch, capsys):
    monkeypatch.setattr(
        "canlib.modes.multi_exec.build_iocontrol_index",
        lambda _data: {"IGPM": {"tx_id": 0x770, "protocol": "UDS", "cmds": {}}},
    )
    asyncio.run(_exec_iocontrol(_sm(), "sjb", "BC01", False, {}, INDEX, False))
    out = capsys.readouterr().out
    assert "Unknown DID BC01 for IGPM" in out  # found the ECU, not "No IOControl DIDs"


class TestMonitor:
    def _controller(self, **kw):
        steps = [{"type": "query", "ecu": "sjb", "pids": []}]
        c = MonitorController(FakeTerminal(), steps, {}, verbose=False, **kw)
        c._ecu_index = INDEX
        return c

    def test_warmup_reads(self):
        assert self._controller()._warmup_reads() == [("IGPM", INDEX["IGPM"], "22BC03")]

    def test_query_label(self):
        assert self._controller().query_label() == "IGPM"

    def test_raw_setup_opens_an_aliased_session(self, monkeypatch):
        client = MagicMock()
        c = self._controller(raw_client=client)
        monkeypatch.setattr("canlib.ecus.pids.build_ecu_index", lambda _data: INDEX)
        asyncio.run(c.setup([{"type": "session", "target": "sjb", "mode": "03"}]))
        reads = [(call.args[0], call.args[1]) for call in client.read.call_args_list]
        assert ("IGPM", bytes.fromhex("1003")) in reads
        assert ("IGPM", bytes.fromhex("22BC03")) in reads
