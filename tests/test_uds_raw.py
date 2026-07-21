"""Tests for RawUdsClient (pipelined UDS over ISO-TP) with fake isotp stacks."""

import pytest

from canlib.transport import uds_raw
from canlib.transport.uds_raw import RawUdsClient, response_id


class FakeStack:
    """Stand-in for isotp.NotifierBasedCanStack keyed by txid + request bytes."""

    def __init__(self, txid, table):
        self.txid = txid
        self.table = table
        self.sent = []
        self._resp = None
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def available(self):
        return False

    def send(self, data, *a, **k):
        self.sent.append(bytes(data))
        self._resp = self.table.get((self.txid, bytes(data)))

    def recv(self, block=False, timeout=None):
        r, self._resp = self._resp, None
        return bytearray(r) if r is not None else None


class FakeNotifier:
    def __init__(self, *a, **k):
        pass

    def add_listener(self, *a, **k):
        pass

    def stop(self):
        pass


def _client(monkeypatch, table, ecus):
    monkeypatch.setattr(uds_raw.can, "Notifier", FakeNotifier)
    monkeypatch.setattr(
        uds_raw.isotp,
        "NotifierBasedCanStack",
        lambda bus, notifier, address=None, params=None: FakeStack(address._txid, table),
    )
    return RawUdsClient(bus=object(), ecus=ecus, timeout=0.3)


def test_response_id_offset():
    assert response_id(0x770) == 0x778
    assert response_id(0x7E4) == 0x7EC


class TestRawUdsClient:
    def test_read_single(self, monkeypatch):
        table = {(0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03FDEE")}
        c = _client(monkeypatch, table, {"IGPM": (0x770, 0x778)})
        assert c.read("IGPM", bytes.fromhex("22BC03")) == bytes.fromhex("62BC03FDEE")

    def test_read_timeout_raises(self, monkeypatch):
        c = _client(monkeypatch, {}, {"IGPM": (0x770, 0x778)})
        with pytest.raises(TimeoutError):
            c.read("IGPM", bytes.fromhex("22BC03"))

    def test_poll_pipelined_maps_all(self, monkeypatch):
        table = {
            (0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03AA"),
            (0x7E4, bytes.fromhex("2101")): bytes.fromhex("6101BB"),
        }
        c = _client(monkeypatch, table, {"IGPM": (0x770, 0x778), "BMS": (0x7E4, 0x7EC)})
        reqs = [("IGPM", bytes.fromhex("22BC03")), ("BMS", bytes.fromhex("2101"))]
        out = c.poll(reqs)
        assert out[("IGPM", bytes.fromhex("22BC03"))] == bytes.fromhex("62BC03AA")
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101BB")

    def test_poll_missing_is_timeouterror(self, monkeypatch):
        table = {(0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03AA")}
        c = _client(monkeypatch, table, {"IGPM": (0x770, 0x778), "BMS": (0x7E4, 0x7EC)})
        reqs = [("IGPM", bytes.fromhex("22BC03")), ("BMS", bytes.fromhex("2199"))]
        out = c.poll(reqs)
        assert out[("IGPM", bytes.fromhex("22BC03"))] == bytes.fromhex("62BC03AA")
        assert isinstance(out[("BMS", bytes.fromhex("2199"))], Exception)

    def test_poll_sends_all_before_collecting(self, monkeypatch):
        # Verify pipelining: every request is sent (the fake records sends).
        table = {
            (0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03AA"),
            (0x7E4, bytes.fromhex("2101")): bytes.fromhex("6101BB"),
        }
        c = _client(monkeypatch, table, {"IGPM": (0x770, 0x778), "BMS": (0x7E4, 0x7EC)})
        c.poll([("IGPM", bytes.fromhex("22BC03")), ("BMS", bytes.fromhex("2101"))])
        assert c._stacks["IGPM"].sent == [bytes.fromhex("22BC03")]
        assert c._stacks["BMS"].sent == [bytes.fromhex("2101")]

    def test_poll_multiple_per_ecu_are_sequential(self, monkeypatch):
        # Several DIDs on one ECU must go one-at-a-time (single ISO-TP stack),
        # while a second ECU is polled in parallel across rounds.
        table = {
            (0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03AA"),
            (0x770, bytes.fromhex("22BC06")): bytes.fromhex("62BC06BB"),
            (0x770, bytes.fromhex("22BC07")): bytes.fromhex("62BC07CC"),
            (0x7E4, bytes.fromhex("2101")): bytes.fromhex("6101DD"),
        }
        c = _client(monkeypatch, table, {"IGPM": (0x770, 0x778), "BMS": (0x7E4, 0x7EC)})
        reqs = [
            ("IGPM", bytes.fromhex("22BC03")),
            ("IGPM", bytes.fromhex("22BC06")),
            ("IGPM", bytes.fromhex("22BC07")),
            ("BMS", bytes.fromhex("2101")),
        ]
        out = c.poll(reqs)
        # All four resolved correctly (the bug was BC06/BC07 timing out).
        assert out[("IGPM", bytes.fromhex("22BC06"))] == bytes.fromhex("62BC06BB")
        assert out[("IGPM", bytes.fromhex("22BC07"))] == bytes.fromhex("62BC07CC")
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101DD")
        # IGPM saw all three, in order.
        assert c._stacks["IGPM"].sent == [
            bytes.fromhex("22BC03"),
            bytes.fromhex("22BC06"),
            bytes.fromhex("22BC07"),
        ]
