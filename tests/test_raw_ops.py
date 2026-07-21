"""Tests for raw-CAN command dispatch (canlib.modes.raw_ops.run_raw)."""

import asyncio

import pytest

from canlib.modes import raw_monitor, raw_ops
from canlib.wican_mode import ModeError


class T:
    def __init__(self, host="1.2.3.4", port=35000, bitrate=500000):
        self.host = host
        self.port = port
        self.bitrate = bitrate


class Args:
    def __init__(self, **kw):
        self.__dict__.update({"multi": None, "monitor": None, "raw": None, "timeout": 1.0})
        self.__dict__.update(kw)


@pytest.fixture
def routed(monkeypatch):
    calls = []
    monkeypatch.setattr(raw_ops, "require_protocol", lambda host, expected: None, raising=False)
    # require_protocol is imported inside run_raw; patch at source.
    import canlib.wican_mode as wm

    monkeypatch.setattr(wm, "require_protocol", lambda host, expected: None)
    import canlib.commands.sniff as sniff

    monkeypatch.setattr(sniff, "_resolve_device_defaults", lambda h, p, b: (35000, 500000))

    async def _mon(args, host, port, bitrate, pids):
        calls.append("monitor")
        return 0

    async def _q(args, host, port, bitrate, pids):
        calls.append("query")
        return 0

    async def _s(args, host, port, bitrate, pids):
        calls.append("single")
        return 0

    monkeypatch.setattr(raw_monitor, "run_raw_monitor", _mon)
    monkeypatch.setattr(raw_ops, "_raw_query", _q)
    monkeypatch.setattr(raw_ops, "_raw_single", _s)
    return calls


def test_routes_to_monitor(routed):
    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"], monitor=1.0), T(), {}))
    assert rc == 0 and routed == ["monitor"]


def test_routes_to_query(routed):
    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"]), T(), {}))
    assert rc == 0 and routed == ["query"]


def test_routes_to_single(routed):
    rc = asyncio.run(raw_ops.run_raw(Args(raw="770:22BC03"), T(), {}))
    assert rc == 0 and routed == ["single"]


def test_unsupported_command_errors(routed):
    rc = asyncio.run(raw_ops.run_raw(Args(), T(), {}))
    assert rc == 2 and routed == []


def test_no_host_errors(routed):
    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"]), T(host=None), {}))
    assert rc == 2 and routed == []


def test_mode_mismatch_errors(monkeypatch, routed):
    import canlib.wican_mode as wm

    def boom(host, expected):
        raise ModeError("wrong mode")

    monkeypatch.setattr(wm, "require_protocol", boom)
    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"]), T(), {}))
    assert rc == 2 and routed == []
