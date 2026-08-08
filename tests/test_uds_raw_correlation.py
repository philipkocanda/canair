"""Request/response correlation on the raw-CAN path (RawUdsClient).

The pipelined raw client had no correlation at all: whatever came off an ECU's
ISO-TP stack was returned as the outstanding request's answer. On a slow link that
is the same fault the ELM327 path had — a request abandoned at its deadline whose
answer arrives moments later becomes the *next* request's answer — except worse,
because nothing raised and nothing was tallied. The values were simply wrong, and
a one-PID poll would report a cycle-old reading indefinitely.

Two defences, tested here:

- **Echo validation** (`uds_parse.payload_echo_mismatch`) rejects a reply whose
  SID+identifier isn't what was asked for. Cheap and immediate, but blind when the
  stale reply echoes the same PID.
- **The owed-response ledger** covers exactly that blind spot: an ISO-TP stack
  delivers one message per exchange, so while an ECU owes earlier answers the next
  messages off its stack *are* those answers, whatever they contain.

Device-free: fake ISO-TP stacks with a test-controlled inbox.
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import pytest

from canlib.addressing import EcuAddress
from canlib.link_latency import LinkLatency
from canlib.transport import uds_raw
from canlib.transport.uds_raw import _MAX_OWED_RESPONSES, RawUdsClient


class QueuedStack:
    """A fake ISO-TP stack whose inbox is a queue, decoupled from sends.

    The existing `FakeStack` answers each send from a lookup table, so it can hold
    only one message and can't express the situation these tests exist for: a stack
    with a *backlog*. Here a test either seeds the inbox directly (a reply already
    queued before the request went out) or attaches messages to a send via
    :meth:`on_send` (a reply that arrives after it).
    """

    def __init__(self, txid: int, params: dict | None = None):
        self.txid = txid
        self.params = params or {}
        self.sent: list[bytes] = []
        self.inbox: deque[bytes] = deque()
        self.on_send: deque[list[str]] = deque()
        self.started = False

    # -- test-facing staging ------------------------------------------------
    def seed(self, *payloads: str) -> None:
        """Queue messages as if they were already reassembled and waiting."""
        self.inbox.extend(bytes.fromhex(p) for p in payloads)

    def answers(self, *rounds: list[str]) -> None:
        """Attach a batch of messages to each upcoming send, in order."""
        self.on_send.extend(rounds)

    # -- isotp.NotifierBasedCanStack surface --------------------------------
    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def available(self) -> bool:
        return bool(self.inbox)

    def send(self, data, *a, **k) -> None:
        self.sent.append(bytes(data))
        if self.on_send:
            self.seed(*self.on_send.popleft())

    def recv(self, block: bool = False, timeout: float | None = None):
        if self.inbox:
            return bytearray(self.inbox.popleft())
        return None


class FakeNotifier:
    def __init__(self, *a, **k):
        pass

    def add_listener(self, *a, **k):
        pass

    def stop(self):
        pass


def _client(monkeypatch, ecus: dict[str, tuple[int, int]], **kw) -> RawUdsClient:
    monkeypatch.setattr(uds_raw.can, "Notifier", FakeNotifier)
    monkeypatch.setattr(uds_raw, "STACK_WARMUP_S", 0.0)  # no real stacks to settle
    monkeypatch.setattr(
        uds_raw.isotp,
        "NotifierBasedCanStack",
        lambda bus, notifier, address=None, params=None: QueuedStack(address._txid, params),
    )
    addresses = {name: EcuAddress(tx, rx) for name, (tx, rx) in ecus.items()}
    kw.setdefault("timeout", 0.05)
    bus_link = kw.pop("bus_link", None)
    bus = SimpleNamespace(link=bus_link) if bus_link is not None else object()
    return RawUdsClient(bus=bus, addresses=addresses, **kw)


def _bms(monkeypatch, **kw) -> tuple[RawUdsClient, QueuedStack]:
    c = _client(monkeypatch, {"BMS": (0x7E4, 0x7EC)}, **kw)
    return c, c._stacks["BMS"]


def _poll(c: RawUdsClient, ecu: str, *pids: str):
    reqs = [(ecu, bytes.fromhex(p)) for p in pids]
    return c.poll(reqs)


class TestEchoValidation:
    """A reply for the wrong identifier is never served as the right one."""

    def test_a_stale_reply_is_discarded_and_the_real_one_returned(self, monkeypatch):
        c, st = _bms(monkeypatch)
        st.answers(["6101AA", "6102BB"])  # last cycle's 2101 answer, then ours
        out = _poll(c, "BMS", "2102")
        assert out[("BMS", bytes.fromhex("2102"))] == bytes.fromhex("6102BB")
        assert c.diag.stale == 1

    def test_a_wrong_sid_is_discarded(self, monkeypatch):
        c, st = _bms(monkeypatch)
        st.answers(["62BC03AA", "6102BB"])
        out = _poll(c, "BMS", "2102")
        assert out[("BMS", bytes.fromhex("2102"))] == bytes.fromhex("6102BB")
        assert c.diag.stale == 1

    def test_a_negative_response_is_still_delivered(self, monkeypatch):
        """An NRC is a real answer to *this* request — it must not look stale."""
        c, st = _bms(monkeypatch)
        st.answers(["7F2231"])
        out = _poll(c, "BMS", "22BC03")
        assert out[("BMS", bytes.fromhex("22BC03"))] == bytes.fromhex("7F2231")
        assert c.diag.stale == 0

    def test_a_non_identifier_request_skips_validation(self, monkeypatch):
        """There is nothing to compare on a `19 02`, so don't invent a verdict."""
        c, st = _bms(monkeypatch)
        st.answers(["5902ABCD"])
        out = _poll(c, "BMS", "1902")
        assert out[("BMS", bytes.fromhex("1902"))] == bytes.fromhex("5902ABCD")
        assert c.diag.stale == 0

    def test_a_batch_is_validated_against_its_first_did(self, monkeypatch):
        c, st = _bms(monkeypatch)
        st.answers(["62BC04AA", "62BC03AABC0400"])
        out = _poll(c, "BMS", "22BC03BC04")
        assert out[("BMS", bytes.fromhex("22BC03BC04"))] == bytes.fromhex("62BC03AABC0400")
        assert c.diag.stale == 1


class TestHkQuirk:
    """The Hyundai/Kia F1xx -1 identity offset is ECU behaviour, not a stale frame."""

    def test_tolerated_when_the_profile_opts_in(self, monkeypatch):
        c, st = _bms(monkeypatch, hk_f1xx_offset=True)
        st.answers(["62F1874B"])
        out = _poll(c, "BMS", "22F188")
        assert out[("BMS", bytes.fromhex("22F188"))] == bytes.fromhex("62F1874B")
        assert c.diag.stale == 0

    def test_rejected_on_a_make_neutral_profile(self, monkeypatch):
        c, st = _bms(monkeypatch, hk_f1xx_offset=False)
        st.answers(["62F1874B"])
        out = _poll(c, "BMS", "22F188")
        assert isinstance(out[("BMS", bytes.fromhex("22F188"))], TimeoutError)
        assert c.diag.stale == 1


class TestOwedResponseLedger:
    """The blind spot echo validation cannot cover: the same PID, one cycle late."""

    def test_an_abandoned_request_leaves_the_ecu_owing_a_response(self, monkeypatch):
        c, _st = _bms(monkeypatch)
        out = _poll(c, "BMS", "2101")
        assert isinstance(out[("BMS", bytes.fromhex("2101"))], TimeoutError)
        assert c._owed["BMS"] == 1

    def test_the_same_pid_recovers_via_the_ledger(self, monkeypatch):
        """The regression: both replies echo `01`, so only the count separates them."""
        c, st = _bms(monkeypatch)
        _poll(c, "BMS", "2101")  # times out; the ECU now owes one answer
        assert c._owed["BMS"] == 1

        # Both land during the next cycle: the debt, then this cycle's reading.
        st.answers(["6101AA", "6101BB"])
        out = _poll(c, "BMS", "2101")
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101BB")
        assert c.diag.stale == 1
        assert c._owed["BMS"] == 0

    def test_a_reply_queued_before_the_send_is_drained(self, monkeypatch):
        """The cheap case: the debt was already settled before the request went out."""
        c, st = _bms(monkeypatch)
        _poll(c, "BMS", "2101")
        st.seed("6101AA")  # the late answer lands *between* cycles
        st.answers(["6101BB"])
        out = _poll(c, "BMS", "2101")
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101BB")
        assert c.diag.stale == 1
        assert c._owed["BMS"] == 0

    def test_a_settled_debt_does_not_discard_the_next_good_reply(self, monkeypatch):
        c, st = _bms(monkeypatch)
        _poll(c, "BMS", "2101")
        st.answers(["6101AA", "6101BB"])
        _poll(c, "BMS", "2101")
        assert c._owed["BMS"] == 0

        st.answers(["6101CC"])
        out = _poll(c, "BMS", "2101")
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101CC")
        assert c.diag.stale == 1  # unchanged from the previous cycle

    def test_an_unpaid_ledger_is_abandoned(self, monkeypatch):
        """Past the cap the answers were lost, not delayed.

        Holding the debt open would discard every genuine reply from here on, so
        the count is dropped and echo validation carries on alone.
        """
        c, st = _bms(monkeypatch)
        for _ in range(_MAX_OWED_RESPONSES - 1):
            _poll(c, "BMS", "2101")
        assert c._owed["BMS"] == _MAX_OWED_RESPONSES - 1
        _poll(c, "BMS", "2101")
        assert c._owed["BMS"] == 0

        st.answers(["6101AA"])
        out = _poll(c, "BMS", "2101")
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101AA")

    def test_a_healthy_link_never_discards(self, monkeypatch):
        c, st = _bms(monkeypatch)
        st.answers(["6101AA"], ["6102BB"], ["6103CC"])
        out = _poll(c, "BMS", "2101", "2102", "2103")
        assert out[("BMS", bytes.fromhex("2103"))] == bytes.fromhex("6103CC")
        assert c.diag.stale == 0
        assert c.diag.errors == 0


class TestPerEcuIsolation:
    """One ECU's backlog must not affect another's — the stacks are independent."""

    def test_a_stale_ecu_does_not_taint_a_healthy_one(self, monkeypatch):
        c = _client(monkeypatch, {"BMS": (0x7E4, 0x7EC), "IGPM": (0x770, 0x778)})
        bms, igpm = c._stacks["BMS"], c._stacks["IGPM"]
        _poll(c, "BMS", "2101")
        assert c._owed["BMS"] == 1
        assert c._owed["IGPM"] == 0

        bms.answers(["6101AA", "6101BB"])
        igpm.answers(["62BC03CC"])
        out = c.poll([("BMS", bytes.fromhex("2101")), ("IGPM", bytes.fromhex("22BC03"))])
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101BB")
        assert out[("IGPM", bytes.fromhex("22BC03"))] == bytes.fromhex("62BC03CC")


class TestResponsePending:
    """A 0x78 is an interim frame, not a stale one."""

    def test_pending_then_the_real_answer(self, monkeypatch):
        c, st = _bms(monkeypatch)
        st.answers(["7F2178", "6101AA"])
        out = _poll(c, "BMS", "2101")
        assert out[("BMS", bytes.fromhex("2101"))] == bytes.fromhex("6101AA")
        assert c.diag.stale == 0


class TestReadPath:
    """`read()` is the single-request path used by `canair read`, not the monitor."""

    def test_a_stale_reply_is_discarded_and_the_fresh_one_returned(self, monkeypatch):
        c, st = _bms(monkeypatch, timeout=0.5)
        st.answers(["6101AA", "6102BB"])
        assert c.read("BMS", bytes.fromhex("2102")) == bytes.fromhex("6102BB")
        assert c.diag.stale == 1

    def test_a_timeout_leaves_the_ecu_owing_a_response(self, monkeypatch):
        c, _st = _bms(monkeypatch)
        with pytest.raises(TimeoutError):
            c.read("BMS", bytes.fromhex("2101"))
        assert c._owed["BMS"] == 1

    def test_the_ledger_carries_into_the_next_read(self, monkeypatch):
        c, st = _bms(monkeypatch, timeout=0.5)
        with pytest.raises(TimeoutError):
            c.read("BMS", bytes.fromhex("2101"))
        st.answers(["6101AA", "6101BB"])
        assert c.read("BMS", bytes.fromhex("2101")) == bytes.fromhex("6101BB")

    def test_a_pending_answer_is_returned_when_the_follow_up_never_comes(self, monkeypatch):
        """Long-standing behaviour, preserved: surface the ECU's own interim reply."""
        c, st = _bms(monkeypatch, timeout=0.05)
        st.answers(["7F2178"])
        assert c.read("BMS", bytes.fromhex("2101")) == bytes.fromhex("7F2178")


class TestPerRequestBudget:
    """A deadline must cover the ECU's think time *and* the network's share."""

    def test_an_unmeasured_link_leaves_the_configured_budget_alone(self, monkeypatch):
        c = _client(monkeypatch, {"BMS": (0x7E4, 0x7EC)}, timeout=1.0)
        assert c._budget("BMS", None) == 1.0

    def test_the_measured_link_is_added_to_the_default(self, monkeypatch):
        c = _client(monkeypatch, {"BMS": (0x7E4, 0x7EC)}, timeout=1.0)
        c.link.seed(0.5)
        assert c._budget("BMS", None) == pytest.approx(1.0 + c.link.budget)

    def test_the_measured_link_is_added_to_a_per_ecu_budget(self, monkeypatch):
        # The profile's response_timeout_ms states what the *car* may take; the
        # network's share is on top of it, not carved out of it.
        c = _client(monkeypatch, {"BMS": (0x7E4, 0x7EC)}, timeout=1.0, ecu_timeouts={"BMS": 4.0})
        c.link.seed(0.5)
        assert c._budget("BMS", None) == pytest.approx(4.0 + c.link.budget)

    def test_a_forced_timeout_is_an_instruction_not_a_budget(self, monkeypatch):
        c = _client(monkeypatch, {"BMS": (0x7E4, 0x7EC)}, timeout=1.0)
        c.link.seed(2.0)
        assert c._budget("BMS", 0.25) == 0.25

    def test_a_bus_estimate_is_adopted_at_construction(self, monkeypatch):
        # RawUdsClient is handed a bus, so it takes the bus's measurement rather
        # than starting a second, blinder one.
        link = LinkLatency()
        link.seed(0.3)
        c = _client(monkeypatch, {"BMS": (0x7E4, 0x7EC)}, bus_link=link)
        assert c.link is link

    def test_the_flow_control_budgets_are_widened_by_the_bus_measurement(self, monkeypatch):
        link = LinkLatency()
        link.seed(1.0)
        c = _client(monkeypatch, {"BMS": (0x7E4, 0x7EC)}, bus_link=link)
        assert c._stacks["BMS"].params["rx_flowcontrol_timeout"] > 1000


class TestTransportChoiceHint:
    """A slow link makes `slcan-tcp` itself the limit; say so once, not per read."""

    @staticmethod
    def _logged(monkeypatch):
        seen: list[tuple[str, str]] = []
        monkeypatch.setattr(
            uds_raw, "log_event", lambda cat, detail="", **kw: seen.append((cat, detail))
        )
        return seen

    def test_a_lan_link_gets_no_advice(self, monkeypatch):
        seen = self._logged(monkeypatch)
        link = LinkLatency()
        link.seed(0.001)
        _bms(monkeypatch, bus_link=link)
        assert seen == []

    def test_an_unmeasured_link_gets_no_advice(self, monkeypatch):
        seen = self._logged(monkeypatch)
        _bms(monkeypatch)
        assert seen == []

    def test_a_slow_link_is_told_the_transport_is_the_limit(self, monkeypatch):
        seen = self._logged(monkeypatch)
        link = LinkLatency()
        link.seed(0.4)
        _bms(monkeypatch, bus_link=link)
        assert len(seen) == 1
        detail = seen[0][1]
        assert "400ms" in detail  # the measured round trip, not the internal allowance
        assert "wican-ws" in detail

    def test_the_advice_is_given_once_per_session(self, monkeypatch):
        seen = self._logged(monkeypatch)
        link = LinkLatency()
        link.seed(0.4)
        c, st = _bms(monkeypatch, bus_link=link)
        st.answers(["6101AA"], ["6101BB"])
        _poll(c, "BMS", "2101")
        _poll(c, "BMS", "2101")
        assert len(seen) == 1
