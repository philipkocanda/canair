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

    def resolve_device_defaults(self, profile_bitrate=None):
        return self.port or 3333, self.bitrate or profile_bitrate or 500000


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
    monkeypatch.setattr(wican_mode, "require_slcan_reachable", lambda host, port, **kw: None)

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


def test_slcan_port_unreachable_errors(monkeypatch, routed):
    """A silent/wedged SLCAN data port fails fast with rc=2, no traceback."""

    def boom(host, port, **kw):
        raise ModeError("port not reachable")

    monkeypatch.setattr(wican_mode, "require_slcan_reachable", boom)
    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"]), T(), {}))
    assert rc == 2 and routed == []
    assert FakeRawTerminal.instances == []  # never got to constructing the terminal


def test_terminal_connect_oserror_is_clean(monkeypatch, routed, capsys):
    """A socket failure at RawTerminal construction is a clean rc=1, not a traceback."""

    def boom(*a, **k):
        raise TimeoutError("timed out")

    import canlib.transport as transport

    monkeypatch.setattr(transport, "RawTerminal", boom)
    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"]), T(), {}))
    assert rc == 1 and routed == []
    # The message names the real reason, not a bare exception repr.
    err = capsys.readouterr().err
    assert "timed out" in err


def test_mid_session_drop_is_clean(monkeypatch, capsys):
    """A transport error DURING the session (e.g. peer close) is caught gracefully."""
    import can

    monkeypatch.setattr(wican_mode, "require_protocol", lambda host, expected, **kw: None)
    monkeypatch.setattr(wican_mode, "require_slcan_reachable", lambda host, port, **kw: None)

    async def _drop(args, terminal, pids, host):
        raise can.CanError("SLCAN TCP connection closed by peer")

    import canlib.commands._live as live

    monkeypatch.setattr(live, "dispatch_mode", _drop)
    import canlib.transport as transport

    FakeRawTerminal.instances = []
    monkeypatch.setattr(transport, "RawTerminal", FakeRawTerminal)

    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"]), T(), {}))
    assert rc == 1  # clean exit code, no traceback
    assert FakeRawTerminal.instances[0].closed is True  # terminal still closed
    err = capsys.readouterr().err
    assert "SLCAN" in err and "closed by peer" in err


def test_mid_session_drop_while_saving_points_at_recover(monkeypatch, capsys):
    """A --save session that drops tells the user their data is recoverable."""
    monkeypatch.setattr(wican_mode, "require_protocol", lambda host, expected, **kw: None)
    monkeypatch.setattr(wican_mode, "require_slcan_reachable", lambda host, port, **kw: None)

    async def _drop(args, terminal, pids, host):
        raise ConnectionResetError("reset by peer")

    import canlib.commands._live as live

    monkeypatch.setattr(live, "dispatch_mode", _drop)
    import canlib.transport as transport

    FakeRawTerminal.instances = []
    monkeypatch.setattr(transport, "RawTerminal", FakeRawTerminal)

    rc = asyncio.run(raw_ops.run_raw(Args(multi=["query BMS"], save=True, label="drive"), T(), {}))
    assert rc == 1
    err = capsys.readouterr().err
    assert "--recover" in err
