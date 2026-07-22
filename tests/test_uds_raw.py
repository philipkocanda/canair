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
        return self._resp is not None

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

    def test_read_waits_through_response_pending(self, monkeypatch):
        # read() must ride out 7F xx 78 (ResponsePending) and return the final
        # answer — parity with RawTerminal + the ELM327 path.
        class SeqStack:
            def __init__(self, txid, seq):
                self.txid = txid
                self._seq = list(seq)
                self._sent = False

            def start(self):
                pass

            def stop(self):
                pass

            def available(self):
                return self._sent and bool(self._seq)

            def send(self, data, *a, **k):
                self._sent = True

            def recv(self, block=False, timeout=None):
                if not self._sent:
                    return None
                return bytearray(self._seq.pop(0)) if self._seq else None

        seq = [bytes.fromhex("7F1978"), bytes.fromhex("5902FF0123002F")]
        monkeypatch.setattr(uds_raw.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(
            uds_raw.isotp,
            "NotifierBasedCanStack",
            lambda bus, notifier, address=None, params=None: SeqStack(address._txid, seq),
        )
        c = RawUdsClient(bus=object(), ecus={"BMS": (0x7E4, 0x7EC)}, timeout=0.3)
        assert c.read("BMS", bytes.fromhex("1902FF")) == bytes.fromhex("5902FF0123002F")

    def test_poll_waits_through_response_pending(self, monkeypatch):
        # poll() must also ride out 0x78: the 0x78 frame is consumed, the ECU is
        # kept pending, and the real answer is returned on a later harvest.
        class SeqStack:
            def __init__(self, txid, seq):
                self.txid = txid
                self._queue = list(seq)  # frames the ECU will emit, in order
                self._sent = False
                self._out = None

            def start(self):
                pass

            def stop(self):
                pass

            def available(self):
                # Only after a request; surface the next queued frame one at a time.
                if not self._sent:
                    return False
                if self._out is None and self._queue:
                    self._out = self._queue.pop(0)
                return self._out is not None

            def send(self, data, *a, **k):
                self._sent = True

            def recv(self, block=False, timeout=None):
                r, self._out = self._out, None
                return bytearray(r) if r is not None else None

        seq = [bytes.fromhex("7F2278"), bytes.fromhex("62BC03FDEE")]
        monkeypatch.setattr(uds_raw.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(
            uds_raw.isotp,
            "NotifierBasedCanStack",
            lambda bus, notifier, address=None, params=None: SeqStack(address._txid, seq),
        )
        c = RawUdsClient(bus=object(), ecus={"IGPM": (0x770, 0x778)}, timeout=0.5)
        out = c.poll([("IGPM", bytes.fromhex("22BC03"))])
        assert out[("IGPM", bytes.fromhex("22BC03"))] == bytes.fromhex("62BC03FDEE")

    def test_poll_slow_ecu_does_not_starve_fast(self, monkeypatch):
        # Regression for the shared-deadline bug: a silent/slow ECU must NOT eat
        # the budget of an ECU collected after it. Each request gets its own
        # deadline and is harvested as soon as it completes.
        import time as _time

        class DelayedStack:
            def __init__(self, txid, resp, ready_after):
                self.txid = txid
                self._resp = resp
                self.ready_after = ready_after
                self._ready_at = None
                self.sent = []

            def start(self):
                pass

            def stop(self):
                pass

            def available(self):
                return (
                    self._resp is not None
                    and self._ready_at is not None
                    and _time.monotonic() >= self._ready_at
                )

            def send(self, data, *a, **k):
                self.sent.append(bytes(data))
                self._ready_at = _time.monotonic() + self.ready_after

            def recv(self, block=False, timeout=None):
                if self.available():
                    r, self._resp = self._resp, None
                    return bytearray(r) if r is not None else None
                return None

        stacks = {
            0x770: DelayedStack(0x770, None, 999),  # SLOW: silent (never answers)
            0x7E4: DelayedStack(0x7E4, bytes.fromhex("6101BB"), 0.1),  # FAST: ready at 0.1s
        }
        monkeypatch.setattr(uds_raw.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(
            uds_raw.isotp,
            "NotifierBasedCanStack",
            lambda bus, notifier, address=None, params=None: stacks[address._txid],
        )
        c = RawUdsClient(
            bus=object(), ecus={"IGPM": (0x770, 0x778), "BMS": (0x7E4, 0x7EC)}, timeout=0.3
        )
        out = c.poll([("IGPM", bytes.fromhex("22BC03")), ("BMS", bytes.fromhex("2101"))])
        # FAST resolves despite SLOW being silent...
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101BB")
        # ...and SLOW times out on its own budget.
        assert isinstance(out[("IGPM", bytes.fromhex("22BC03"))], TimeoutError)


class TestPollCallback:
    def test_on_result_fires_per_request(self, monkeypatch):
        table = {
            (0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03AA"),
            (0x7E4, bytes.fromhex("2101")): bytes.fromhex("6101BB"),
        }
        c = _client(monkeypatch, table, {"IGPM": (0x770, 0x778), "BMS": (0x7E4, 0x7EC)})
        seen: dict = {}
        out = c.poll(
            [("IGPM", bytes.fromhex("22BC03")), ("BMS", bytes.fromhex("2101"))],
            on_result=lambda key, val: seen.__setitem__(key, bytes(val)),
        )
        # Callback fired once per request with the same values as the return map.
        assert seen == out
        assert seen[("IGPM", bytes.fromhex("22BC03"))] == bytes.fromhex("62BC03AA")

    def test_on_result_fires_on_timeout(self, monkeypatch):
        c = _client(monkeypatch, {}, {"BMS": (0x7E4, 0x7EC)})  # empty table -> timeout
        seen = []
        c.poll([("BMS", bytes.fromhex("2199"))], on_result=lambda key, val: seen.append(val))
        assert len(seen) == 1 and isinstance(seen[0], Exception)
