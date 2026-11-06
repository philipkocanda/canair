"""Tests for raw-CAN live commands (canair.live.read.raw_ops.run_raw)."""

import asyncio
from typing import ClassVar

import pytest

from canair.cli.profile_context import active
from canair.device.wican import mode as wican_mode
from canair.device.wican.mode import ModeError
from canair.live.monitor import raw_run as raw_monitor
from canair.live.monitor.options import MonitorOptions
from canair.live.read import raw_ops
from canair.live.session import SessionOptions
from canair.transport.config import ConnectionOptions


def T(host="1.2.3.4", port=35000, bitrate=500000):
    from canair.transport.config import TransportConfig

    return TransportConfig(type="slcan-tcp", host=host, port=port, bitrate=bitrate)


@pytest.fixture(autouse=True)
def _http_port_open(monkeypatch):
    """The pre-flight's HTTP-port check answers, so the 4.x mode check runs."""
    monkeypatch.setattr("canair.core.tcp.tcp_open", lambda host, port, timeout: True)


MONITOR = MonitorOptions(query_steps=[{"type": "query", "ecu": "BMS", "pids": []}])


def run_raw(transport, *, operation=None, monitor=None, session=None):
    """``raw_ops.run_raw`` with the options a bare command would pass."""

    async def _nothing(terminal, pids_data):
        return None

    return asyncio.run(
        raw_ops.run_raw(
            transport,
            {},
            conn=ConnectionOptions(),
            session=session or SessionOptions(),
            operation=operation or _nothing,
            monitor=monitor,
            profile=active(),
        )
    )


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

    async def _mon(monitor, host, port, bitrate, pids, **kw):
        calls.append("monitor")
        return 0

    monkeypatch.setattr(raw_monitor, "run_raw_monitor", _mon)

    import canair.transport as transport

    FakeRawTerminal.instances = []
    monkeypatch.setattr(transport, "RawTerminal", FakeRawTerminal)

    async def _operation(terminal, pids):
        calls.append(("operation", type(terminal).__name__))

    return calls, _operation


def test_routes_monitor_to_optimized_path(routed):
    calls, operation = routed
    assert run_raw(T(), operation=operation, monitor=MONITOR) == 0
    assert calls == ["monitor"]
    assert FakeRawTerminal.instances == []  # no RawTerminal for monitor


def test_runs_the_operation_over_a_raw_terminal(routed):
    calls, operation = routed
    assert run_raw(T(), operation=operation) == 0
    assert calls == [("operation", "FakeRawTerminal")]
    assert FakeRawTerminal.instances[0].closed is True  # cleaned up


def test_no_host_errors(routed):
    calls, operation = routed
    assert run_raw(T(host=None), operation=operation) == 2
    assert calls == []


def test_mode_mismatch_errors(monkeypatch, routed):
    def boom(host, expected, **kw):
        raise ModeError("wrong mode")

    monkeypatch.setattr(wican_mode, "require_protocol", boom)
    calls, operation = routed
    assert run_raw(T(), operation=operation) == 2
    assert calls == []


def test_slcan_port_unreachable_errors(monkeypatch, routed):
    """A silent/wedged SLCAN data port fails fast with rc=2, no traceback."""

    def boom(host, port, **kw):
        raise ModeError("port not reachable")

    monkeypatch.setattr(wican_mode, "require_slcan_reachable", boom)
    calls, operation = routed
    assert run_raw(T(), operation=operation) == 2
    assert calls == []
    assert FakeRawTerminal.instances == []  # never got to constructing the terminal


def test_terminal_connect_oserror_is_clean(monkeypatch, routed, capsys):
    """A socket failure at RawTerminal construction is a clean rc=1, not a traceback."""

    def boom(*a, **k):
        raise TimeoutError("timed out")

    import canair.transport as transport

    monkeypatch.setattr(transport, "RawTerminal", boom)
    calls, operation = routed
    assert run_raw(T(), operation=operation) == 1
    assert calls == []
    # The message names the real reason, not a bare exception repr.
    err = capsys.readouterr().err
    assert "timed out" in err


def test_mid_session_drop_is_clean(monkeypatch, capsys):
    """A transport error DURING the session (e.g. peer close) is caught gracefully."""
    import can

    monkeypatch.setattr(wican_mode, "require_protocol", lambda host, expected, **kw: None)
    monkeypatch.setattr(wican_mode, "require_slcan_reachable", lambda host, port, **kw: None)

    async def _drop(terminal, pids):
        raise can.CanError("SLCAN TCP connection closed by peer")

    import canair.transport as transport

    FakeRawTerminal.instances = []
    monkeypatch.setattr(transport, "RawTerminal", FakeRawTerminal)

    rc = run_raw(T(), operation=_drop)
    assert rc == 1  # clean exit code, no traceback
    assert FakeRawTerminal.instances[0].closed is True  # terminal still closed
    err = capsys.readouterr().err
    assert "SLCAN" in err and "closed by peer" in err


def test_mid_session_drop_while_saving_points_at_recover(monkeypatch, capsys):
    """A --save session that drops tells the user their data is recoverable."""
    monkeypatch.setattr(wican_mode, "require_protocol", lambda host, expected, **kw: None)
    monkeypatch.setattr(wican_mode, "require_slcan_reachable", lambda host, port, **kw: None)

    async def _drop(terminal, pids):
        raise ConnectionResetError("reset by peer")

    import canair.transport as transport

    FakeRawTerminal.instances = []
    monkeypatch.setattr(transport, "RawTerminal", FakeRawTerminal)

    rc = run_raw(T(), operation=_drop, session=SessionOptions(saving=True))
    assert rc == 1
    err = capsys.readouterr().err
    assert "--recover" in err
