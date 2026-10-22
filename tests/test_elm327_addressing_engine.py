"""Tests for the ELM327 engine's addressing (``Elm327Terminal.set_header`` and friends).

Over a fake adapter that answers like an STN/ELM327: ``OK`` to an AT command,
``ELM327 v2.3`` to a reset, and ``?`` to ``ATFCSM1`` until both ``ATFCSH`` and
``ATFCSD`` are set (the datasheet's "Altering Flow Control Messages"). Covers what a
review found the engine getting wrong: an init-time rejection disabling mode 1 for
the session, a rejected header letting a request reach the previous ECU, state kept
across a resync, an adapter reset or a long idle gap, and shared request ids.
"""

import asyncio

import pytest

from canlib.addressing import AddressBook, AddressingMode, EcuAddress
from canlib.transport.elm327_addressing import IDLE_FORGET_SECONDS
from canlib.transport.elm327_terminal import Elm327Terminal
from canlib.transport.errors import (
    AdapterRefusedError,
    describe_transport_error,
    is_transport_error,
)

LBC = EcuAddress(0x79B, 0x7BB)
BMS = EcuAddress(0x7E4, 0x7EC)
RENAULT = EcuAddress(0x18DB33F1, 0x18DAF1DB, AddressingMode.NORMAL_29BIT, fc_id=0x18DADBF1)
LBC_PLAN = ["ATSH79B", "ATFCSH79B", "ATFCSD300000", "ATFCSM1", "ATCRA7BB"]


class FakeAdapter:
    """A scripted ELM327 that enforces the mode-1 prerequisites.

    ``replies`` overrides a command's answer (a list is consumed one per send);
    ``silent`` maps a command to how many times it goes unanswered.
    """

    transport_name = "elm327-tcp"

    def __init__(self, replies=None, silent=None):
        self.sent: list[str] = []
        self._replies = dict(replies or {})
        self._silent = dict(silent or {})
        self._fc_header = self._fc_data = False
        self._q: asyncio.Queue[str] = asyncio.Queue()

    async def connect(self) -> None:
        pass

    async def send(self, text: str) -> None:
        cmd = text.rstrip("\r")
        self.sent.append(cmd)
        if self._silent.get(cmd, 0) > 0:
            self._silent[cmd] -= 1
            return
        await self._q.put(self._answer(cmd) + "\r>")

    def _answer(self, cmd: str) -> str:
        scripted = self._replies.get(cmd)
        if isinstance(scripted, list):
            scripted = scripted.pop(0) if scripted else None
        if scripted is not None:
            return scripted
        if cmd == "ATZ":
            self._fc_header = self._fc_data = False
            return "ELM327 v2.3"
        if cmd == "ATI":
            return "ELM327 v2.3"
        if cmd.startswith("ATFCSH"):
            self._fc_header = True
        elif cmd.startswith("ATFCSD"):
            self._fc_data = True
        elif cmd == "ATFCSM1" and not (self._fc_header and self._fc_data):
            return "?"
        return "OK" if cmd.startswith("AT") else "NO DATA"

    async def recv(self, timeout: float) -> str | None:
        try:
            return self._q.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0.001)
            return None

    async def drain(self, per_recv_timeout: float = 0.2, max_seconds: float = 1.0) -> None:
        while not self._q.empty():
            self._q.get_nowait()

    async def close(self) -> None:
        pass


def _terminal(*addresses: EcuAddress, **adapter) -> tuple[Elm327Terminal, FakeAdapter]:
    ch = FakeAdapter(**adapter)
    term = Elm327Terminal(ch, timeout=0.05)
    term.addresses = AddressBook({a.tx_id: a for a in addresses})
    return term, ch


class TestInitRejection:
    @pytest.mark.asyncio
    async def test_a_misordered_init_does_not_disable_mode_1(self):
        # Upstream-style init: ATFCSM1 before ATFCSH, which the adapter rejects.
        term, ch = _terminal(LBC)
        await term.init_elm("ATSP6;ATFCSM1;ATFCSD300000;")
        ch.sent.clear()
        await term.set_header(0x79B)
        assert "ATFCSM1" in ch.sent
        assert term._address.fc_mode == 1

    @pytest.mark.asyncio
    async def test_nor_after_a_reconnect(self):
        term, ch = _terminal(LBC)
        for _ in range(2):
            await term.connect()
            await term.init_elm("ATSP6;ATFCSM1;")
            ch.sent.clear()
            await term.set_header(0x79B)
            assert ch.sent == LBC_PLAN


class TestRefusedHeader:
    @pytest.mark.asyncio
    async def test_a_rejected_header_fails_before_any_request(self):
        term, ch = _terminal(BMS, RENAULT, replies={"ATSH18DB33F1": "?"})
        await term.set_header(0x7E4)
        with pytest.raises(AdapterRefusedError, match="8-digit"):
            async with term.transaction():
                await term.set_header(0x18DB33F1)
                await term.send_uds("22F190", expected_sid=0x22)
        # Nothing went out after the refused header: the adapter still points at BMS.
        assert ch.sent[-1] == "ATSH18DB33F1"

    @pytest.mark.asyncio
    async def test_the_header_is_retried_rather_than_skipped(self):
        term, ch = _terminal(BMS, replies={"ATSH7E4": ["?"]})
        with pytest.raises(AdapterRefusedError):
            await term.set_header(0x7E4)
        await term.set_header(0x7E4)
        assert ch.sent == ["ATSH7E4", "ATSH7E4", "ATFCSH7E4"]

    @pytest.mark.asyncio
    async def test_rejected_flow_control_header_fails_an_explicit_ecu(self):
        term, _ = _terminal(LBC, replies={"ATFCSH79B": "?"})
        with pytest.raises(AdapterRefusedError, match="ATFCSH79B"):
            await term.set_header(0x79B)

    @pytest.mark.asyncio
    async def test_but_not_a_conventional_ecu_in_mode_0(self):
        # Mode 0 derives the flow-control id itself, so an old clone that lacks
        # ATFCSH keeps working, and pays for the rejection once.
        term, ch = _terminal(BMS, replies={"ATFCSH7E4": "?"})
        await term.set_header(0x7E4)
        await term.set_header(0x7E4)
        assert ch.sent == ["ATSH7E4", "ATFCSH7E4"]

    def test_the_error_reads_as_a_refusal_not_a_dropped_link(self):
        exc = AdapterRefusedError("it answered ATSH18DB33F1 with '?'")
        assert is_transport_error(exc)
        text = describe_transport_error(exc, host="h", transport_label="WebSocket", saving=True)
        assert text.startswith("WebSocket adapter to h refused a command — it answered")
        assert "dropped off" not in text
        assert "captures uds --recover" in text


class TestUnanswered:
    @pytest.mark.asyncio
    async def test_a_header_whose_reply_never_came_is_sent_again(self):
        term, ch = _terminal(BMS, silent={"ATSH7E4": 1})
        await term.set_header(0x7E4)
        assert ch.sent.count("ATSH7E4") == 2
        assert term._cur_header == 0x7E4

    @pytest.mark.asyncio
    async def test_a_header_never_confirmed_is_a_connection_error(self):
        term, _ = _terminal(BMS, silent={"ATSH7E4": 99})
        with pytest.raises(ConnectionError, match="never confirmed ATSH7E4") as info:
            await term.set_header(0x7E4)
        assert not isinstance(info.value, AdapterRefusedError)


class TestResyncAndReset:
    @pytest.mark.asyncio
    async def test_a_resync_makes_the_next_header_restate_everything(self):
        term, ch = _terminal(LBC)
        await term.set_header(0x79B)
        await term._resync("test")
        ch.sent.clear()
        await term.set_header(0x79B)
        assert ch.sent == LBC_PLAN

    @pytest.mark.asyncio
    async def test_a_retry_after_a_resync_readdresses_first(self):
        term, ch = _terminal(LBC, replies={"2101": ["GARBAGE", "6101AABB"]})
        await term.set_header(0x79B)
        ch.sent.clear()
        resp = await term.send_uds("2101", expected_sid=0x21, expected_echo=b"\x01", retries=1)
        assert resp["ok"]
        assert ch.sent == ["2101", "ATI", *LBC_PLAN, "2101"]

    @pytest.mark.asyncio
    async def test_a_reset_banner_in_place_of_a_reply_restates_the_addressing(self):
        # The adapter reset itself (or a connect banner arrived late): the reply
        # does not parse, the pipe resyncs, and the retry re-addresses from scratch.
        term, ch = _terminal(LBC, replies={"2101": ["ELM327 v2.3", "6101AABB"]})
        await term.set_header(0x79B)
        ch.sent.clear()
        resp = await term.send_uds("2101", expected_sid=0x21, expected_echo=b"\x01", retries=1)
        assert resp["ok"]
        assert ch.sent == ["2101", "ATI", *LBC_PLAN, "2101"]

    @pytest.mark.asyncio
    async def test_a_banner_answering_an_addressing_command_forces_a_replan(self):
        # A banner in ATFCSH's slot: the header sent before it is gone, so
        # set_header plans again and re-sends it rather than trusting it.
        term, ch = _terminal(LBC, replies={"ATFCSH79B": ["ELM327 v2.3"]})
        await term.set_header(0x79B)
        assert ch.sent == [*LBC_PLAN, "ATSH79B"]
        assert term._cur_header == 0x79B

    @pytest.mark.asyncio
    async def test_an_identification_command_may_print_the_banner(self):
        term, _ = _terminal()
        assert await term.send_command("ATI") == "ELM327 v2.3"
        assert await term.send_command("AT WS", timeout=0.05) == "OK"


class TestIdleGap:
    @pytest.mark.asyncio
    async def test_a_long_gap_restates_the_addressing(self):
        now = [100.0]
        term, ch = _terminal(BMS)
        term._clock = lambda: now[0]
        await term.set_header(0x7E4)
        now[0] += IDLE_FORGET_SECONDS - 1
        await term.set_header(0x7E4)
        assert ch.sent == ["ATSH7E4", "ATFCSH7E4"]
        # Another client (a WiCAN's own AutoPID) may have re-pointed the adapter.
        now[0] += IDLE_FORGET_SECONDS + 1
        await term.set_header(0x7E4)
        assert ch.sent[2:] == ["ATSH7E4", "ATFCSH7E4", "ATCRA"]


class TestSharedRequestId:
    @pytest.mark.asyncio
    async def test_a_shared_request_id_is_addressed_as_a_broadcast(self, monkeypatch):
        events = []
        monkeypatch.setattr(
            "canlib.transport.elm327_terminal.log_event", lambda *a, **k: events.append(a)
        )
        evc = {"tx_id": 0x18DB33F1, "rx_id": 0x18DAF1DB, "addressing": {"fc_id": 0x18DADBF1}}
        bms = {"tx_id": 0x18DB33F1, "rx_id": 0x18DAF1DA, "addressing": {"fc_id": 0x18DADAF1}}
        term, ch = _terminal()
        term.addresses = AddressBook.from_profile(
            {"addressing": {"mode": "normal_29bit"}, "ecus": {"EVC": evc, "BMS": bms}}
        )
        await term.set_header(0x79B)  # an explicit ECU first: mode 1, pinned filter
        ch.sent.clear()
        await term.set_header(0x18DB33F1)
        await term.set_header(0x18DB33F1)
        assert ch.sent == ["ATSH18DB33F1", "ATFCSH18DB33F1", "ATFCSM0", "ATCRA"]
        assert len([e for e in events if "several ECUs" in e[1]]) == 1


class TestLinkProbes:
    @pytest.mark.asyncio
    async def test_addressing_commands_feed_the_link_estimate(self):
        term, _ = _terminal(LBC)
        await term.set_header(0x79B)
        assert term.link.n == len(LBC_PLAN)
        await term.send_command("2101")
        assert term.link.n == len(LBC_PLAN)
