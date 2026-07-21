"""Tests for raw-CAN command dispatch (canlib.modes.raw_ops.run_raw)."""

import asyncio
from typing import ClassVar

import pytest

from canlib import wican_mode
from canlib.modes import raw_monitor, raw_ops
from canlib.wican_mode import ModeError


class T:
    def __init__(self, host="1.2.3.4", port=35000, bitrate=500000):
        self.host = host
        self.port = port
        self.bitrate = bitrate


class Args:
    def __init__(self, **kw):
        self.__dict__.update(
            {"multi": None, "monitor": None, "raw": None, "verbose": False, "unsafe": False}
        )
        self.__dict__.update(kw)


class FakeRawTerminal:
    instances: ClassVar[list] = []

    def __init__(self, *a, **k):
        FakeRawTerminal.instances.append(self)
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.fixture
def routed(monkeypatch):
    calls = []
    monkeypatch.setattr(wican_mode, "require_protocol", lambda host, expected, **kw: None)

    import canlib.commands.sniff as sniff

    monkeypatch.setattr(sniff, "_resolve_device_defaults", lambda h, p, b: (35000, 500000))

    async def _mon(args, host, port, bitrate, pids):
        calls.append("monitor")
        return 0

    monkeypatch.setattr(raw_monitor, "run_raw_monitor", _mon)

    async def _dispatch(args, terminal, pids, host):
        calls.append(("dispatch", type(terminal).__name__))

    import canlib.commands._live as live

    monkeypatch.setattr(live, "dispatch_mode", _dispatch)

    import canlib.transport as transport

    FakeRawTerminal.instances = []
    monkeypatch.setattr(transport, "RawTerminal", FakeRawTerminal)
    return calls


def test_routes_monitor_to_optimized_path(routed):
    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"], monitor=1.0), T(), {}))
    assert rc == 0 and routed == ["monitor"]
    assert FakeRawTerminal.instances == []  # no RawTerminal for monitor


def test_routes_query_to_dispatch(routed):
    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"]), T(), {}))
    assert rc == 0 and routed == [("dispatch", "FakeRawTerminal")]
    assert FakeRawTerminal.instances[0].closed is True  # cleaned up


def test_routes_scan_to_dispatch(routed):
    rc = asyncio.run(raw_ops.run_raw(Args(scan=True, tx="7E4"), T(), {}))
    assert rc == 0 and routed == [("dispatch", "FakeRawTerminal")]


def test_no_host_errors(routed):
    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"]), T(host=None), {}))
    assert rc == 2 and routed == []


def test_mode_mismatch_errors(monkeypatch, routed):
    def boom(host, expected, **kw):
        raise ModeError("wrong mode")

    monkeypatch.setattr(wican_mode, "require_protocol", boom)
    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"]), T(), {}))
    assert rc == 2 and routed == []
