"""The raw monitor's sessions: opened through SessionManager, kept alive between polls."""

from __future__ import annotations

import asyncio
import time

from canair.live.monitor.sessions import MonitorSessions
from canair.safety.blocklist import SessionPolicy
from canair.transport.stats import TransportStats
from canair.transport.timing import TimingRecorder
from tests.support.monitor_controller import make_controller

PIDS = {
    "ecus": {
        "SKM": {
            "tx_id": 0x7A5,
            "identity": {"id_protocol": "UDS"},
            "wake": {"method": "rapid_read", "prime_pid": "22B003", "attempts": 3},
        },
        "BMS": {"tx_id": 0x7E4, "identity": {"id_protocol": "KWP2000"}},
    }
}


class RawClient:
    """The slice of RawUdsClient the raw monitor's sessions use; records reads."""

    def __init__(self, replies: dict[bytes, bytes] | None = None):
        self.reads: list[tuple[str, bytes]] = []
        self.replies = replies or {}
        self.diag = TransportStats(transport="slcan-tcp")
        self.timings = TimingRecorder()
        self.session_policy = SessionPolicy()

    def read(self, ecu, req, timeout=None):
        req = bytes(req)
        self.reads.append((ecu, req))
        if req in self.replies:
            return self.replies[req]
        if req[0] == 0x10:
            return bytes([0x50, req[1]])
        if req == bytes.fromhex("3E00"):
            return bytes.fromhex("7E00")
        return bytes([req[0] + 0x40]) + req[1:]


def _ready(steps, client=None):
    client = client or RawClient()
    ctrl = make_controller(None, [], PIDS, verbose=False, raw_client=client)
    asyncio.run(ctrl.setup(steps))
    return ctrl, client


def test_a_session_step_wakes_with_the_ecus_ritual():
    ctrl, client = _ready([{"type": "session", "target": "SKM", "wake": True}])
    assert client.reads[0] == ("SKM", bytes.fromhex("22B003"))
    assert ("SKM", bytes.fromhex("1003")) in client.reads
    assert ctrl.sessions.active_modes() == {"SKM": "03"}


def test_an_idle_session_gets_a_keepalive_between_polls():
    ctrl, client = _ready([{"type": "session", "target": "BMS", "mode": "81"}])
    ctrl.sm.table.track(0x7E4, at=time.monotonic() - 10)
    client.reads.clear()
    asyncio.run(ctrl.sessions.keep_alive())
    assert client.reads == [("BMS", bytes.fromhex("3E00"))]


def test_a_session_the_poll_keeps_busy_gets_none():
    ctrl, client = _ready([{"type": "session", "target": "BMS", "mode": "81"}])
    ctrl.sm.table.track(0x7E4, at=time.monotonic() - 10)
    ctrl.sessions.note_answer("BMS")  # a poll was just answered
    client.reads.clear()
    asyncio.run(ctrl.sessions.keep_alive())
    assert client.reads == []


def test_an_ecu_without_tester_present_is_not_asked_again():
    client = RawClient({bytes.fromhex("3E00"): bytes.fromhex("7F3E11")})
    ctrl, client = _ready([{"type": "session", "target": "BMS", "mode": "81"}], client)
    for _ in range(3):
        ctrl.sm.table.track(0x7E4, at=time.monotonic() - 10)
        asyncio.run(ctrl.sessions.keep_alive())
    assert client.reads.count(("BMS", bytes.fromhex("3E00"))) == 1


def test_nothing_is_sent_without_a_session():
    ctrl, client = _ready([])
    assert isinstance(ctrl.sessions, MonitorSessions)
    asyncio.run(ctrl.sessions.keep_alive())
    assert client.reads == []
