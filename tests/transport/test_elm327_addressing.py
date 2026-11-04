"""Tests for ELM327 addressing: receive filter and flow control per ECU.

The pure policy (canlib.transport.elm327_addressing) and the engine that applies it
(Elm327Terminal.set_header), over a scripted channel. The cars: the Ioniq's
conventional ``tx + 0x08`` ECUs must send exactly what they always did; the Leaf's
``0x79B -> 0x7BB`` and ``0x797 -> 0x79A`` need the filter and flow-control mode 1;
a Renault-style functional-request ECU needs its physical reply pinned.
"""

import asyncio

import pytest

from canlib.addressing import AddressBook, AddressingMode, EcuAddress
from canlib.transport.elm327_addressing import (
    AUTO,
    DEFAULT_FC_DATA,
    FC_MODE_ADAPTER,
    FC_MODE_USER,
    UNKNOWN,
    AdapterState,
    ElmTarget,
    elm_id,
    flow_control_data,
    functional_target,
    is_conventional,
    pinned_target,
    target_for,
    target_in,
)
from canlib.transport.elm327_terminal import Elm327Terminal

LBC = EcuAddress(0x79B, 0x7BB)
VCM = EcuAddress(0x797, 0x79A)
BMS = EcuAddress(0x7E4, 0x7EC)
RENAULT = EcuAddress(0x18DB33F1, 0x18DAF1DB, AddressingMode.NORMAL_29BIT, fc_id=0x18DADBF1)
FIXED_29 = EcuAddress(0x18DA10F1, 0x18DAF110, AddressingMode.NORMAL_FIXED_29BIT)


class TestPolicy:
    @pytest.mark.parametrize(
        ("addr", "conventional"),
        [
            (BMS, True),
            (FIXED_29, True),
            (LBC, False),  # +0x20
            (EcuAddress(0x704, 0x784), False),  # +0x80
            (EcuAddress(0x761, 0x762), False),  # +0x01
            (EcuAddress(0x6B4, 0x694), False),  # negative offset
            (VCM, False),  # an explicit per-ECU rx_id
            (RENAULT, False),
            (EcuAddress(0x6F1, 0x6F9, AddressingMode.NORMAL_EXTENDED_11BIT), False),
            (EcuAddress(0x7E4, 0x7EC, fc_id=0x7E0), False),
        ],
    )
    def test_conventional(self, addr, conventional):
        assert is_conventional(addr) is conventional

    def test_conventional_target_leaves_the_adapter_alone(self):
        assert target_for(BMS) == ElmTarget(header=0x7E4, fc_header=0x7E4, rx_id=0x7EC)
        assert not target_for(BMS).explicit

    @pytest.mark.parametrize(
        "addr",
        [
            # BMW 0x6F1: several ECUs share the request id, and ATCEA is unsupported.
            EcuAddress(0x6F1, 0x607, AddressingMode.NORMAL_EXTENDED_11BIT),
            # A Leaf-offset discovery sweep past 0x7DF: no 11-bit reply id exists.
            EcuAddress(0x7F0, 0x810),
            EcuAddress(0x10, -0x10),
        ],
    )
    def test_unaddressable_ecus_keep_the_defaults(self, addr):
        target = target_for(addr)
        assert not target.explicit and target.fc_mode is None and target.rx_id is None

    def test_registered_broadcast_ecu_without_fc_id_is_functional(self):
        # An OBD-II "ENGINE" registered at 0x7DF resolves rx 0x7E7, but answers 0x7E8-F.
        assert target_for(EcuAddress(0x7DF, 0x7E7)) == functional_target(0x7DF)

    def test_offset_target_pins_both_ids(self):
        assert target_for(LBC) == ElmTarget(0x79B, 0x79B, rx_filter=0x7BB, fc_mode=FC_MODE_USER)

    def test_functional_request_ecu_sends_flow_control_to_its_physical_id(self):
        target = target_for(RENAULT)
        assert (target.header, target.fc_header, target.rx_filter) == (
            0x18DB33F1,
            0x18DADBF1,
            0x18DAF1DB,
        )

    def test_pinned_target_pins_a_conventional_ecu_too(self):
        assert pinned_target(BMS) == ElmTarget(0x7E4, 0x7E4, rx_filter=0x7EC, fc_mode=FC_MODE_USER)

    def test_pinned_target_keeps_a_broadcast_on_the_auto_filter(self):
        assert pinned_target(EcuAddress(0x7DF, 0x7E7)).rx_filter == AUTO

    def test_unregistered_broadcast_restores_the_adapter_defaults(self):
        target = functional_target(0x7DF)
        assert target.rx_filter == AUTO and target.fc_mode == FC_MODE_ADAPTER

    def test_elm_id_width(self):
        assert (elm_id(0x7BB), elm_id(0x18DAF1DB)) == ("7BB", "18DAF1DB")
        assert elm_id(0x7E0, extended=True) == "000007E0"

    def test_flow_control_data_follows_isotp_stmin_only(self):
        assert flow_control_data(None) == DEFAULT_FC_DATA == "300000"
        # The block size stays 0: in mode 1 the adapter sends flow control only in
        # answer to a First Frame, so a non-zero one would stall after one block.
        assert flow_control_data({"blocksize": 8, "stmin": 10}) == "30000A"
        assert flow_control_data({"blocksize": 8}) == "300000"
        assert flow_control_data({"stmin": 0x1FF}) == "3000FF"


class TestAdapterState:
    def test_conventional_ecu_after_reset_sends_only_the_header_pair(self):
        state = AdapterState()
        assert state.plan(target_for(BMS)) == ["ATSH7E4", "ATFCSH7E4"]

    def test_offset_ecu_sends_the_mode_1_prerequisites_in_order(self):
        assert AdapterState().plan(target_for(LBC)) == [
            "ATSH79B",
            "ATFCSH79B",
            "ATFCSD300000",
            "ATFCSM1",
            "ATCRA7BB",
        ]

    def _at(self, target):
        state = AdapterState()
        for cmd in state.plan(target):
            state.observe(cmd, "OK")
        return state

    def test_nothing_is_resent_for_the_same_ecu(self):
        assert self._at(target_for(LBC)).plan(target_for(LBC)) == []

    def test_explicit_to_explicit_moves_only_what_differs(self):
        state = self._at(target_for(LBC))
        assert state.plan(target_for(VCM)) == ["ATSH797", "ATFCSH797", "ATCRA79A"]

    def test_explicit_to_conventional_restores_the_filter_and_keeps_mode_1(self):
        state = self._at(target_for(LBC))
        assert state.plan(target_for(BMS)) == ["ATSH7E4", "ATFCSH7E4", "ATCRA"]

    def test_broadcast_after_an_explicit_ecu_restores_mode_0(self):
        state = self._at(target_for(LBC))
        assert state.plan(functional_target(0x7DF)) == ["ATSH7DF", "ATFCSH7DF", "ATFCSM0", "ATCRA"]

    def test_forgotten_state_is_restated(self):
        state = AdapterState()
        state.forget()
        assert state.plan(target_for(BMS)) == ["ATSH7E4", "ATFCSH7E4", "ATCRA"]

    def test_conventional_ecu_keeps_the_init_baseline_filter(self):
        # The ELM327-Emulator fixture's init filters to its primary ECU with ATCRA7E8.
        state = AdapterState()
        state.observe("ATCRA7E8", "OK")
        state.mark_baseline()
        assert state.plan(target_for(EcuAddress(0x7E0, 0x7E8))) == ["ATSH7E0", "ATFCSH7E0"]
        for cmd in state.plan(target_for(LBC)):
            state.observe(cmd, "OK")
        assert state.plan(target_for(BMS)) == ["ATSH7E4", "ATFCSH7E4", "ATCRA7E8"]

    def test_filter_already_on_the_ecus_own_reply_is_left_alone(self):
        state = AdapterState()
        state.observe("ATCRA7EC", "OK")
        assert state.plan(target_for(BMS)) == ["ATSH7E4", "ATFCSH7E4"]

    def test_a_rejected_command_is_not_planned_again_until_a_reset(self):
        state = AdapterState()
        for cmd in state.plan(target_for(LBC)):
            state.observe(cmd, "?" if cmd == "ATFCSM1" else "OK")
        assert state.plan(target_for(LBC)) == []
        state.observe("ATZ", "ELM327 v2.3")
        assert "ATFCSM1" in state.plan(target_for(LBC))

    def test_can_flow_control_switch_is_not_a_filter(self):
        state = AdapterState()
        state.observe("ATCFC1", "OK")
        assert state.rx_filter == AUTO

    def test_reset_commands_restore_the_defaults(self):
        state = self._at(target_for(LBC))
        state.observe("ATZ", "ELM327 v2.3")
        assert (state.header, state.rx_filter, state.fc_mode) == (None, AUTO, FC_MODE_ADAPTER)

    def test_a_rejected_command_leaves_its_value_unknown(self):
        state = AdapterState()
        state.observe("ATFCSM1", "?")
        assert state.fc_mode is UNKNOWN
        state.observe("ATCRA7BB", "?")
        assert state.rx_filter is UNKNOWN

    def test_direct_sends_keep_the_cache_coherent(self):
        state = AdapterState()
        state.observe("AT CRA 7BB", "OK")
        assert state.rx_filter == 0x7BB
        state.observe("ATAR", "OK")
        assert state.rx_filter == AUTO
        state.observe("ATCF7E8", "OK")
        assert state.rx_filter is UNKNOWN


class ScriptedChannel:
    """Answers each command from a script ("OK" for anything unscripted)."""

    transport_name = "elm327-tcp"

    def __init__(self, replies: dict[str, str] | None = None):
        self.sent: list[str] = []
        self._replies = replies or {}
        self._q: asyncio.Queue[str] = asyncio.Queue()

    async def connect(self) -> None:
        pass

    async def send(self, text: str) -> None:
        cmd = text.rstrip("\r")
        self.sent.append(cmd)
        await self._q.put(self._replies.get(cmd, "OK") + "\r>")

    async def recv(self, timeout: float) -> str | None:
        try:
            return self._q.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0)
            return None

    async def drain(self, per_recv_timeout: float = 0.2, max_seconds: float = 1.0) -> None:
        pass

    async def close(self) -> None:
        pass


def _terminal(replies=None, **addresses) -> tuple[Elm327Terminal, ScriptedChannel]:
    ch = ScriptedChannel(replies)
    term = Elm327Terminal(ch)
    term.addresses = AddressBook(
        {a.tx_id: a for a in addresses.values()}, {"addressing": {"rx_offset": 0x20}}
    )
    return term, ch


class TestTerminal:
    @pytest.mark.asyncio
    async def test_conventional_ecu_sends_what_it_always_did(self):
        term, ch = _terminal(BMS=BMS)
        await term.set_header(0x7E4)
        await term.set_header(0x7E4)
        assert ch.sent == ["ATSH7E4", "ATFCSH7E4"]

    @pytest.mark.asyncio
    async def test_registered_offset_ecu(self):
        term, ch = _terminal(LBC=LBC)
        await term.set_header(0x79B)
        assert ch.sent == ["ATSH79B", "ATFCSH79B", "ATFCSD300000", "ATFCSM1", "ATCRA7BB"]

    @pytest.mark.asyncio
    async def test_unregistered_id_resolves_from_the_profile_offset(self):
        term, ch = _terminal()
        await term.set_header(0x743)
        assert ch.sent[-1] == "ATCRA763"

    @pytest.mark.asyncio
    async def test_registered_functional_ecu_pins_its_physical_reply(self):
        term, ch = _terminal(RENAULT=RENAULT)
        await term.set_header(0x18DB33F1)
        assert ch.sent == [
            "ATSH18DB33F1",
            "ATFCSH18DADBF1",
            "ATFCSD300000",
            "ATFCSM1",
            "ATCRA18DAF1DB",
        ]

    @pytest.mark.asyncio
    async def test_init_reset_means_no_extra_commands_for_a_conventional_ecu(self):
        term, ch = _terminal(BMS=BMS)
        await term.connect()
        await term.init_elm("ATSP6;")
        ch.sent.clear()
        await term.set_header(0x7E4)
        assert ch.sent == ["ATSH7E4", "ATFCSH7E4"]

    @pytest.mark.asyncio
    async def test_connect_without_reset_restates_the_filter(self):
        term, ch = _terminal(BMS=BMS)
        await term.connect()
        await term.set_header(0x7E4)
        assert ch.sent == ["ATSH7E4", "ATFCSH7E4", "ATCRA"]

    @pytest.mark.asyncio
    async def test_rejected_command_is_logged_once_and_not_resent(self, monkeypatch):
        events = []
        monkeypatch.setattr(
            "canlib.transport.elm327_terminal.log_event", lambda *a, **k: events.append(a)
        )
        term, ch = _terminal({"ATFCSM1": "?"}, LBC=LBC)
        await term.set_header(0x79B)
        await term.set_header(0x743)
        assert ch.sent.count("ATFCSM1") == 1
        assert [e[1] for e in events] == [
            "adapter rejected ATFCSM1 (addressing 0x79B) with '?'; its previous setting stays"
        ]

    @pytest.mark.asyncio
    async def test_init_rejection_is_shown_with_verbose(self, capsys):
        term, _ = _terminal({"ATFCSM1": "?"})
        term.verbose = True
        await term.init_elm("ATSP6;ATFCSM1;")
        assert "rejected ATFCSM1 (profile init)" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_init_filter_survives_a_conventional_ecu(self):
        engine = EcuAddress(0x7E0, 0x7E8)
        term, ch = _terminal(ENGINE=engine)
        await term.init_elm("ATSP6;ATCRA7E8;")
        ch.sent.clear()
        await term.set_header(0x7E0)
        assert ch.sent == ["ATSH7E0", "ATFCSH7E0"]

    @pytest.mark.asyncio
    async def test_flow_control_data_comes_from_the_terminal(self):
        term, ch = _terminal(LBC=LBC)
        term.flow_control_data = "30080A"
        await term.set_header(0x79B)
        assert "ATFCSD30080A" in ch.sent

    @pytest.mark.asyncio
    async def test_broadcast_after_an_explicit_ecu_lets_every_responder_through(self):
        term, ch = _terminal(LBC=LBC)
        await term.set_header(0x79B)
        ch.sent.clear()
        await term.set_header(0x7DF)
        assert ch.sent == ["ATSH7DF", "ATFCSH7DF", "ATFCSM0", "ATCRA"]

    @pytest.mark.asyncio
    async def test_registered_broadcast_ecu_is_not_pinned(self):
        term, ch = _terminal(OBD=EcuAddress(0x7DF, 0x7E7))
        await term.set_header(0x7DF)
        assert ch.sent == ["ATSH7DF", "ATFCSH7DF"]


class TestAddressBook:
    def test_from_profile_and_fallback(self):
        book = AddressBook.from_profile(
            {
                "addressing": {"rx_offset": 0x20},
                "ecus": {"VCM": {"tx_id": 0x797, "rx_id": 0x79A}, "LBC": {"tx_id": 0x79B}},
            }
        )
        assert book.resolve(0x797).rx_id == 0x79A
        assert book.resolve(0x79B).rx_id == 0x7BB
        assert book.resolve(0x743).rx_id == 0x763  # unregistered: profile default

    def test_unregistered_id_honours_the_profile_fc_id(self):
        # The same resolution a registered ECU gets (resolve_ecu_address).
        book = AddressBook.from_profile({"addressing": {"fc_id": 0x7E0}, "ecus": {}})
        assert book.resolve(0x7E4) == EcuAddress(0x7E4, 0x7EC, fc_id=0x7E0)

    def test_unregistered_id_under_normal_fixed_29bit(self):
        book = AddressBook.from_profile({"addressing": {"mode": "normal_fixed_29bit"}})
        assert book.resolve(0x18DA10F1) == FIXED_29
        assert book.mode == AddressingMode.NORMAL_FIXED_29BIT

    @pytest.mark.parametrize("mode", ["normal_extended_11bit", "extended_29bit"])
    def test_unregistered_id_under_the_extended_modes(self, mode):
        book = AddressBook.from_profile({"addressing": {"mode": mode, "target_address": 0x12}})
        addr = book.resolve(0x6F1)
        assert (addr.mode, addr.target_address) == (AddressingMode(mode), 0x12)
        assert not target_for(addr).explicit

    def test_ecus_sharing_a_request_id_are_flagged(self):
        book = AddressBook.from_profile(
            {
                "ecus": {
                    "EVC": {"tx_id": 0x18DB33F1, "rx_id": 0x18DAF1DB},
                    "BMS": {"tx_id": 0x18DB33F1, "rx_id": 0x18DAF1DA},
                    "TWIN": {"tx_id": 0x7E4},
                    "ALIAS": {"tx_id": 0x7E4},  # same address twice: not ambiguous
                },
            }
        )
        assert book.shared == frozenset({0x18DB33F1})
        assert not book.is_shared(0x7E4)


class TestTargetIn:
    def _book(self, **profile) -> AddressBook:
        return AddressBook.from_profile(profile)

    def test_shared_request_id_falls_back_to_the_adapter_defaults(self):
        book = self._book(
            ecus={
                "EVC": {"tx_id": 0x18DB33F1, "rx_id": 0x18DAF1DB, "addressing": {"fc_id": 1}},
                "BMS": {"tx_id": 0x18DB33F1, "rx_id": 0x18DAF1DA, "addressing": {"fc_id": 2}},
            }
        )
        assert target_in(book, 0x18DB33F1) == functional_target(0x18DB33F1)

    def test_unregistered_broadcast_ignores_a_profile_fc_id(self):
        # The 0x7DF TesterPresent must let every responder through.
        book = self._book(addressing={"fc_id": 0x7E0})
        assert target_in(book, 0x7DF) == functional_target(0x7DF)

    def test_registered_ecu_resolves_normally(self):
        book = self._book(addressing={"rx_offset": 0x20}, ecus={"LBC": {"tx_id": 0x79B}})
        assert target_in(book, 0x79B) == target_for(LBC)


class TestPinnedParity:
    """An AutoPID entry in a pinned profile must leave the adapter where the live path does."""

    @pytest.mark.parametrize(
        "addr",
        [
            EcuAddress(0x6F1, 0x607, AddressingMode.NORMAL_EXTENDED_11BIT),
            EcuAddress(0x7F0, 0x810),  # a reply id 11-bit cannot carry
        ],
    )
    def test_unaddressable_ecu_restores_the_auto_filter(self, addr):
        assert pinned_target(addr).rx_filter == AUTO
        # Live: after an explicit ECU, the same ECU gets the (auto) baseline back.
        state = AdapterState()
        for cmd in state.plan(target_for(LBC)):
            state.observe(cmd, "OK")
        assert "ATCRA" in state.plan(target_for(addr))
