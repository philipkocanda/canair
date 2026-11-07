"""Tests for RawUdsClient (pipelined UDS over ISO-TP) with fake isotp stacks."""

import threading
import time
from typing import ClassVar

import pytest

from canair.transport.raw import uds_client as uds_raw
from canair.transport.raw.uds_client import RawUdsClient, response_id
from canair.uds.addressing import EcuAddress
from tests.support.uds_raw import FakeNotifier, FakeStack

# Resolve the lazily imported stack factory now: its module subclasses
# isotp.NotifierBasedCanStack when it loads, and the tests below replace that class.
# Run alone, this file would otherwise load it after the patch, with a lambda as base.
assert uds_raw.build_isotp_stack is not None


def _addrs(mapping: dict[str, tuple[int, int]]) -> dict[str, EcuAddress]:
    """Convert a {name: (tx, rx)} test map to {name: EcuAddress} (11-bit)."""
    return {name: EcuAddress(tx, rx) for name, (tx, rx) in mapping.items()}


def _client(monkeypatch, table, ecus):
    monkeypatch.setattr(uds_raw.can, "Notifier", FakeNotifier)
    monkeypatch.setattr(
        uds_raw.isotp,
        "NotifierBasedCanStack",
        lambda bus, notifier, address=None, params=None: FakeStack(address._txid, table),
    )
    return RawUdsClient(bus=object(), addresses=_addrs(ecus), timeout=0.3)


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

    def test_interrupt_before_poll_returns_immediately(self, monkeypatch):
        # A pre-interrupted client aborts at the loop entry: no per-ECU timeout wait.
        c = _client(monkeypatch, {}, {"BMS": (0x7E4, 0x7EC)})
        c.interrupt()
        t0 = time.monotonic()
        out = c.poll([("BMS", bytes.fromhex("2101"))])
        assert time.monotonic() - t0 < 0.1  # did not wait the 0.3s ECU timeout
        assert out == {}

    def test_interrupt_mid_poll_aborts_before_timeout(self, monkeypatch):
        # An ECU that never answers would burn the full 0.3s per-ECU timeout;
        # interrupting mid-poll (from another thread, as a signal handler does)
        # must abort well before that.
        c = _client(monkeypatch, {}, {"BMS": (0x7E4, 0x7EC)})  # empty table => silent
        result: dict = {}

        def _run():
            result["out"] = c.poll([("BMS", bytes.fromhex("2101"))])
            result["done_at"] = time.monotonic()

        th = threading.Thread(target=_run)
        t0 = time.monotonic()
        th.start()
        time.sleep(0.05)
        c.interrupt()
        th.join(timeout=1.0)
        assert not th.is_alive()
        assert result["done_at"] - t0 < 0.25  # aborted before the 0.3s timeout
        assert result["out"] == {}

    def test_close_sets_interrupt(self, monkeypatch):
        c = _client(monkeypatch, {}, {"BMS": (0x7E4, 0x7EC)})
        c.close()
        assert c._interrupt.is_set()

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
        c = RawUdsClient(bus=object(), addresses=_addrs({"BMS": (0x7E4, 0x7EC)}), timeout=0.3)
        assert c.read("BMS", bytes.fromhex("1902FF")) == bytes.fromhex("5902FF0123002F")

    @pytest.mark.parametrize("stale", ["7F2231", "7F2278"])
    def test_read_skips_a_negative_reply_to_another_service(self, monkeypatch, stale):
        # A refusal or a ResponsePending naming 0x22 is a late answer to an earlier read:
        # it is stale for this 0x19 request, not its answer.
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
                return False

            def send(self, data, *a, **k):
                self._sent = True

            def recv(self, block=False, timeout=None):
                if not self._sent:
                    return None
                return bytearray(self._seq.pop(0)) if self._seq else None

        seq = [bytes.fromhex(stale), bytes.fromhex("5902FF0123002F")]
        monkeypatch.setattr(uds_raw.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(
            uds_raw.isotp,
            "NotifierBasedCanStack",
            lambda bus, notifier, address=None, params=None: SeqStack(address._txid, seq),
        )
        c = RawUdsClient(bus=object(), addresses=_addrs({"BMS": (0x7E4, 0x7EC)}), timeout=0.3)
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
        c = RawUdsClient(bus=object(), addresses=_addrs({"IGPM": (0x770, 0x778)}), timeout=0.5)
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
            bus=object(),
            addresses=_addrs({"IGPM": (0x770, 0x778), "BMS": (0x7E4, 0x7EC)}),
            timeout=0.3,
        )
        out = c.poll([("IGPM", bytes.fromhex("22BC03")), ("BMS", bytes.fromhex("2101"))])
        # FAST resolves despite SLOW being silent...
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101BB")
        # ...and SLOW times out on its own budget.
        assert isinstance(out[("IGPM", bytes.fromhex("22BC03"))], TimeoutError)


class TestRawUdsClientAddressing:
    """__init__ must thread each ECU's fc_id and full EcuAddress into
    build_isotp_stack (gap G-I extended-11-bit / gap G-J functional-TX). Without
    this, an extended-addressing or functional-TX ECU builds a plain stack.
    """

    def test_fc_id_and_address_threaded_per_ecu(self, monkeypatch):
        from canair.uds.addressing import AddressingMode

        seen: dict[str, dict] = {}

        def _build(bus, notifier, address, params, *, fc_id=None):
            # address is the isotp.Address; capture the tx id + fc_id per stack.
            seen[address._txid] = fc_id
            return FakeStack(address._txid, {})

        monkeypatch.setattr(uds_raw.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(uds_raw, "build_isotp_stack", _build)
        monkeypatch.setattr(uds_raw.time, "sleep", lambda *_a, **__: None)  # skip warmup settle

        addresses = {
            # Renault functional-TX 29-bit with a physical FC override (gap G-J).
            "EVC": EcuAddress(
                0x18DB33F1, 0x18DAF1DB, AddressingMode.NORMAL_29BIT, fc_id=0x18DADBF1
            ),
            # BMW extended-11-bit (gap G-I): no fc_id -> None.
            "DME": EcuAddress(
                0x6F1,
                0x612,
                AddressingMode.NORMAL_EXTENDED_11BIT,
                target_address=0x12,
                source_address=0xF1,
            ),
            # Plain 11-bit ECU: no fc_id -> None.
            "BMS": EcuAddress(0x7E4, 0x7EC),
        }
        RawUdsClient(bus=object(), addresses=addresses, timeout=0.3)
        assert seen[0x18DB33F1] == 0x18DADBF1
        assert seen[0x6F1] is None
        assert seen[0x7E4] is None


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


class TestRawUdsClientEcuIsotp:
    """The monitor's client builds each stack from the profile block plus the ECU's own."""

    def test_only_that_ecus_stack_gets_its_flow_control(self, monkeypatch):
        seen: dict[int, dict] = {}

        def _build(bus, notifier, address, params, *, fc_id=None):
            seen[address._txid] = params
            return FakeStack(address._txid, {})

        monkeypatch.setattr(uds_raw.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(uds_raw, "build_isotp_stack", _build)
        monkeypatch.setattr(uds_raw.time, "sleep", lambda *_a, **__: None)
        RawUdsClient(
            bus=object(),
            addresses=_addrs({"EWP": (0x746, 0x74E), "BMS": (0x7E4, 0x7EC)}),
            timeout=0.3,
            ecu_isotp={0x746: {"blocksize": 8}},
        )
        assert seen[0x746]["blocksize"] == 8
        assert seen[0x7E4]["blocksize"] == 0


class _SlowStack(FakeStack):
    """A FakeStack whose reply is available ``delay`` seconds after the request."""

    def __init__(self, txid, table, delay):
        super().__init__(txid, table)
        self.delay = delay
        self.sent_at: list[float] = []
        self._ready_at = 0.0

    def send(self, data, *a, **k):
        super().send(data)
        self.sent_at.append(time.monotonic())
        self._ready_at = time.monotonic() + self.delay

    def available(self):
        return self._resp is not None and time.monotonic() >= self._ready_at

    def recv(self, block=False, timeout=None):
        if block:
            end = time.monotonic() + (timeout or 0.0)
            while not self.available() and time.monotonic() < end:
                time.sleep(0.001)
        if not self.available():
            return None
        return super().recv()


def _slow_client(monkeypatch, table, ecus, delays):
    monkeypatch.setattr(uds_raw.can, "Notifier", FakeNotifier)
    monkeypatch.setattr(uds_raw, "STACK_WARMUP_S", 0.0)
    monkeypatch.setattr(
        uds_raw.isotp,
        "NotifierBasedCanStack",
        lambda bus, notifier, address=None, params=None: _SlowStack(
            address._txid, table, delays.get(address._txid, 0.0)
        ),
    )
    return RawUdsClient(bus=object(), addresses=_addrs(ecus), timeout=1.0)


class TestOneExchangePerEcu:
    """read() from another thread interleaves with poll() request by request, per ECU."""

    TABLE: ClassVar = {
        (0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03AA"),
        (0x770, bytes.fromhex("22BC06")): bytes.fromhex("62BC06BB"),
        (0x770, bytes.fromhex("2FBC0C03")): bytes.fromhex("6FBC0C03"),
        (0x7E4, bytes.fromhex("2101")): bytes.fromhex("6101CC"),
        (0x7E4, bytes.fromhex("2102")): bytes.fromhex("6102DD"),
    }
    ECUS: ClassVar = {"IGPM": (0x770, 0x778), "BMS": (0x7E4, 0x7EC)}

    def test_a_read_on_another_ecu_does_not_wait_for_the_poll(self, monkeypatch):
        c = _slow_client(monkeypatch, self.TABLE, self.ECUS, {0x7E4: 0.3})
        poll = threading.Thread(
            target=c.poll, args=([("BMS", bytes.fromhex("2101")), ("BMS", bytes.fromhex("2102"))],)
        )
        poll.start()
        time.sleep(0.02)
        t0 = time.monotonic()
        assert c.read("IGPM", bytes.fromhex("2FBC0C03")) == bytes.fromhex("6FBC0C03")
        assert time.monotonic() - t0 < 0.1
        poll.join(timeout=2.0)

    def test_a_read_on_a_polled_ecu_waits_for_one_request_not_the_poll(self, monkeypatch):
        c = _slow_client(monkeypatch, self.TABLE, self.ECUS, {0x770: 0.15})
        reqs = [("IGPM", bytes.fromhex("22BC03")), ("IGPM", bytes.fromhex("22BC06"))]
        out: dict = {}
        poll = threading.Thread(target=lambda: out.update(c.poll(reqs)))
        poll.start()
        time.sleep(0.03)  # the first poll request is in flight
        t0 = time.monotonic()
        assert c.read("IGPM", bytes.fromhex("2FBC0C03")) == bytes.fromhex("6FBC0C03")
        waited = time.monotonic() - t0
        poll.join(timeout=2.0)
        # It waited for the request in flight and its own exchange (0.15 s each), not
        # for the poll's second request as well.
        assert waited < 0.4
        sent = c._stacks["IGPM"].sent
        assert sent == [bytes.fromhex(h) for h in ("22BC03", "2FBC0C03", "22BC06")]
        assert out[("IGPM", bytes.fromhex("22BC06"))] == bytes.fromhex("62BC06BB")

    def test_poll_skips_a_held_ecu_and_sends_it_later(self, monkeypatch):
        c = _slow_client(monkeypatch, self.TABLE, self.ECUS, {})
        lock = c._ecu_locks["IGPM"]
        lock.acquire()
        threading.Timer(0.05, lock.release).start()
        out = c.poll([("IGPM", bytes.fromhex("22BC03")), ("BMS", bytes.fromhex("2101"))])
        assert out[("IGPM", bytes.fromhex("22BC03"))] == bytes.fromhex("62BC03AA")
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101CC")
        assert c._stacks["BMS"].sent_at[0] < c._stacks["IGPM"].sent_at[0]

    def test_an_interrupted_poll_releases_its_locks(self, monkeypatch):
        c = _slow_client(monkeypatch, {}, self.ECUS, {})  # silent: the request stays in flight
        poll = threading.Thread(target=c.poll, args=([("BMS", bytes.fromhex("2101"))],))
        poll.start()
        time.sleep(0.05)
        c.interrupt()
        poll.join(timeout=1.0)
        assert c._ecu_locks["BMS"].acquire(blocking=False)


class TestEnsureEcu:
    def test_an_unknown_ecu_is_refused_by_read(self, monkeypatch):
        c = _client(monkeypatch, {}, {"BMS": (0x7E4, 0x7EC)})
        with pytest.raises(LookupError, match="IGPM"):
            c.read("IGPM", bytes.fromhex("22BC03"))

    def test_an_unknown_ecu_fails_its_own_poll_requests_only(self, monkeypatch):
        table = {(0x7E4, bytes.fromhex("2101")): bytes.fromhex("6101BB")}
        c = _client(monkeypatch, table, {"BMS": (0x7E4, 0x7EC)})
        out = c.poll([("IGPM", bytes.fromhex("22BC03")), ("BMS", bytes.fromhex("2101"))])
        assert isinstance(out[("IGPM", bytes.fromhex("22BC03"))], LookupError)
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101BB")

    def test_ensure_adds_a_stack_once(self, monkeypatch):
        table = {(0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03AA")}
        c = _client(monkeypatch, table, {"BMS": (0x7E4, 0x7EC)})
        monkeypatch.setattr(uds_raw, "STACK_WARMUP_S", 0.0)
        c.ensure_ecu("IGPM", EcuAddress(0x770, 0x778))
        stack = c._stacks["IGPM"]
        c.ensure_ecu("IGPM", EcuAddress(0x770, 0x778))
        assert c._stacks["IGPM"] is stack and stack.started
        assert c.read("IGPM", bytes.fromhex("22BC03")) == bytes.fromhex("62BC03AA")
