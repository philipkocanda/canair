"""``discover --functional`` edge cases: when to stop, whom to flow-control, and what to report.

Device-free regression tests for the functional sweep's collector, both transports'
exchanges and the report. The happy paths are in ``test_discover_functional.py``.
"""

from __future__ import annotations

import can
import pytest

from canlib.addressing import EcuAddress
from canlib.modes.functional_sweep import classify_round
from canlib.transport.functional import (
    ABSOLUTE_CAP_S,
    CONSECUTIVE_FRAME_TIMEOUT_S,
    FunctionalCollector,
    FunctionalMessage,
    FunctionalResult,
)
from canlib.transport.functional_raw import raw_functional_exchange

# --------------------------------------------------------------------------- collector


class TestWhenToStop:
    def test_a_stalled_responder_is_given_up_on_despite_other_traffic(self):
        c = FunctionalCollector(0x7DF, bytes.fromhex("22F18C"), started=0.0, window=0.3)
        assert c.feed(0x7EC, bytes.fromhex("100A62F18C313233"), 0.01)  # never continues
        t = 0.02
        while t < 3.0:  # another responder keeps the bus busy
            c.feed(0x778, bytes.fromhex("037F2231AAAAAAAA"), t)
            if c.done(t):
                break
            t += 0.1
        assert t <= 0.01 + CONSECUTIVE_FRAME_TIMEOUT_S + 0.1
        assert c.result().lost == 1

    def test_a_late_frame_of_an_abandoned_message_is_ignored(self):
        c = FunctionalCollector(0x7DF, bytes.fromhex("22F18C"), started=0.0, window=0.3)
        c.feed(0x7EC, bytes.fromhex("100A62F18C313233"), 0.01)
        c.feed(0x7EC, bytes.fromhex("21343536373839AA"), 1.5)  # past N_Cr
        result = c.result()
        assert (result.messages, result.lost) == ([], 1)

    def test_there_is_an_absolute_cap(self):
        c = FunctionalCollector(0x7DF, bytes.fromhex("22F18C"), started=0.0, window=0.3)
        c.feed(0x7EC, bytes.fromhex("1FFF62F18C313233"), 0.01)  # a 4095-byte answer
        t, seq = 0.01, 1
        while not c.done(t):
            t += 0.5
            c.feed(0x7EC, bytes([0x20 | (seq & 0x0F)]) + bytes(7), t)
            seq += 1
            assert t < 60, "never stopped"
        assert t <= 0.3 + ABSOLUTE_CAP_S + 0.5
        assert c.remaining(t) == pytest.approx(0.005)


class TestWhomToFlowControl:
    def test_another_testers_request_gets_no_flow_control(self):
        c = FunctionalCollector(0x7DF, bytes.fromhex("22F18C"), started=0.0, window=0.3)
        # A multi-frame WriteDataByIdentifier on 0x7E0: a request, not an answer.
        assert c.feed(0x7E0, bytes.fromhex("100A2EF190313233"), 0.01) is False
        assert c.done(0.31)  # and it does not hold the window open

    def test_a_malformed_first_frame_gets_no_flow_control(self):
        c = FunctionalCollector(0x7DF, bytes.fromhex("22F18C"), started=0.0, window=0.3)
        assert c.feed(0x7EC, bytes.fromhex("100562F18C31AAAA"), 0.01) is False

    def test_a_negative_first_frame_is_answered(self):
        c = FunctionalCollector(0x7DF, bytes.fromhex("22F18C"), started=0.0, window=0.3)
        assert c.feed(0x7EC, bytes.fromhex("10087F2231AAAAAA"), 0.01) is True

    def test_the_frames_own_format_decides(self):
        c = FunctionalCollector(0x7DF, bytes.fromhex("3E00"), started=0.0, window=0.3)
        c.feed(0x7EC, bytes.fromhex("027E00AAAAAAAAAA"), 0.01, extended=True)
        assert c.result().messages == []


class _Notifier:
    def __init__(self):
        self.listeners = []

    def add_listener(self, cb):
        self.listeners.append(cb)

    def remove_listener(self, cb):
        self.listeners.remove(cb)

    def deliver(self, can_id: int, hex_: str, extended: bool = False) -> None:
        msg = can.Message(arbitration_id=can_id, data=bytes.fromhex(hex_), is_extended_id=extended)
        for cb in list(self.listeners):
            cb(msg)


class _Bus:
    def __init__(self, notifier: _Notifier, script):
        self.notifier, self.script, self.sent = notifier, script, []

    def send(self, msg: can.Message, timeout=None) -> None:
        self.sent.append(msg)
        if msg.arbitration_id == 0x7DF:
            self.script(self.notifier)


async def _raw(script, *, request: str = "22F18C") -> tuple[FunctionalResult, list[can.Message]]:
    notifier = _Notifier()
    bus = _Bus(notifier, script)
    result = await raw_functional_exchange(
        bus,  # type: ignore[arg-type]
        notifier,  # type: ignore[arg-type]
        tx_id=0x7DF,
        request=bytes.fromhex(request),
        padding=0xAA,
        window=0.05,
        flow_control_to=lambda rx: rx - 8,
    )
    return result, bus.sent[1:]


class TestRawExchange:
    @pytest.mark.asyncio
    async def test_no_flow_control_for_a_foreign_first_frame(self):
        def script(n: _Notifier) -> None:
            n.deliver(0x7E0, "100A2EF190313233")  # another tester's request
            n.deliver(0x7EC, "100562F18C31AAAA")  # malformed: 5 bytes belong in an SF

        _, after = await _raw(script)
        assert after == []

    @pytest.mark.asyncio
    async def test_an_extended_frame_on_an_11bit_sweep_is_ignored(self):
        def script(n: _Notifier) -> None:
            n.deliver(0x7EC, "037F2231AAAAAAAA", extended=True)
            n.deliver(0x778, "037F2231AAAAAAAA")

        result, _ = await _raw(script)
        assert [m.rx_id for m in result.messages] == [0x778]


class _RawTerminalStub:
    """Just the state ``RawTerminal._functional_fc_target`` reads."""

    def __init__(self, addr_map, stacks):
        from canlib.addressing import DEFAULT_MODE, DEFAULT_RX_OFFSET

        self.addr_map = addr_map
        self._stacks = dict.fromkeys(stacks)
        self.rx_offset = DEFAULT_RX_OFFSET
        self.mode = DEFAULT_MODE


class TestRawTerminal:
    def _target(self, stub, rx_id):
        from canlib.transport.raw_terminal import RawTerminal

        return RawTerminal._functional_fc_target(stub, rx_id)  # type: ignore[arg-type]

    def test_an_ecu_with_its_own_stack_is_left_to_it_even_with_an_fc_id(self):
        addr = EcuAddress(0x7A0, 0x7C0, fc_id=0x7A5)
        assert self._target(_RawTerminalStub({0x7A0: addr}, [0x7A0]), 0x7C0) is None
        assert self._target(_RawTerminalStub({0x7A0: addr}, []), 0x7C0) == 0x7A5

    def test_an_unregistered_stack_is_matched_by_its_reply_id(self):
        assert self._target(_RawTerminalStub({}, [0x7C0]), 0x7C8) is None
        assert self._target(_RawTerminalStub({}, [0x7C0]), 0x7D8) == 0x7D0

    @pytest.mark.asyncio
    async def test_can_fd_is_refused(self):
        from canlib.transport.raw_terminal import RawTerminal

        term = RawTerminal.__new__(RawTerminal)
        term._classic_can = False
        with pytest.raises(ValueError, match="classic CAN"):
            await term.send_functional(0x7DF, "3E00")


# --------------------------------------------------------------------------- report

ECUS = {
    0x7E4: {"name": "BMS", "rx_id": 0x7EC, "can_bus": ["P-CAN"]},
    0x770: {"name": "IGPM", "rx_id": 0x778, "can_bus": ["B-CAN"]},
}


class TestPendingOnly:
    def _sweep(self):
        return classify_round(
            FunctionalResult(
                0x7DF,
                bytes.fromhex("190208"),
                [
                    FunctionalMessage(0x7EC, bytes.fromhex("7F1978"), 20),
                    FunctionalMessage(0x7EC, bytes.fromhex("7F1978"), 2000),
                    FunctionalMessage(0x7C8, bytes.fromhex("7F1978"), 30),
                    FunctionalMessage(0x778, bytes.fromhex("5902FF"), 40),
                ],
            ),
            ECUS,
        )

    def test_a_known_ecu_that_only_said_pending_is_not_silent(self):
        sweep = self._sweep()
        assert sweep.silent == []
        bms = next(r for r in sweep.responders if r.name == "BMS")
        assert (bms.status, bms.pending, bms.answered, bms.elapsed_ms) == (
            "pending",
            2,
            False,
            2000,
        )
        assert not bms.positive

    def test_an_unknown_one_does_not_vanish(self):
        unknown = [r for r in self._sweep().responders if not r.known]
        assert [(r.rx_id, r.status) for r in unknown] == [(0x7C8, "pending")]

    def test_a_final_reply_still_wins(self):
        igpm = next(r for r in self._sweep().responders if r.name == "IGPM")
        assert (igpm.status, igpm.nrc, igpm.positive) == ("positive", None, True)


# --------------------------------------------------------------------------- the command


@pytest.fixture
def profile_with(tmp_path):
    from canlib import profile
    from canlib.pids import clear_cache

    saved = profile._active

    def make(extra: str):
        root = tmp_path / "prof"
        (root / "ecus").mkdir(parents=True)
        (root / "profile.yaml").write_text(f'car_model: "T"\ninit: "ATSP6;"\n{extra}')
        profile.set_active(str(root))
        clear_cache()

    yield make
    profile._active = saved
    clear_cache()


class TestProfileRefusals:
    def test_normal_29bit_is_refused(self, profile_with):
        from canlib.commands.discover import _profile_error

        profile_with("addressing:\n  mode: normal_29bit\n")
        assert "normal_29bit" in (_profile_error() or "")

    def test_can_fd_is_refused(self, profile_with):
        from canlib.commands.discover import _profile_error

        profile_with("isotp:\n  can_fd: true\n")
        assert "CAN-FD" in (_profile_error() or "")

    def test_fixed_29bit_and_classic_pass(self, profile_with):
        from canlib.commands.discover import _profile_error

        profile_with("addressing:\n  mode: normal_fixed_29bit\n")
        assert _profile_error() is None
