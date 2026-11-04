"""Tests for the shared ISO-TP stack factory (canlib.transport.isotp_stack).

Covers the flow-control arbitration-id override (gap G-J: functional-TX /
physical-RX ECUs like Renault/Mitsubishi). See
plans/2026-07-28-multi-vehicle-support.md.
"""

import time

import can
import isotp

from canlib.addressing import AddressingMode, EcuAddress, build_isotp_address
from canlib.transport.isotp_params import build_isotp_params
from canlib.transport.isotp_stack import _FcAddressStack, build_isotp_stack


class _FakeBus(can.BusABC):
    def __init__(self):
        self.channel_info = "fake"
        super().__init__(channel="x")

    def send(self, msg, timeout=None):
        pass

    def _recv_internal(self, timeout=None):
        return None, False

    def shutdown(self):
        # Chain up: BusABC.shutdown() sets the _is_shutdown flag its __del__
        # checks, otherwise GC logs "…was not properly shut down" at an
        # arbitrary later moment (noise in an unrelated test's output).
        super().shutdown()


class _QueuedBus(can.BusABC):
    """A mock bus that records TX frames and delivers a queued RX frame on demand.

    ``_recv_internal`` withholds the queued frame until ``release`` is set, so the
    ISO-TP stack's ``start()`` has time to register its listener before the frame
    arrives (the Notifier thread would otherwise drain the bus and drop the frame
    before the stack is listening).
    """

    def __init__(self, queued: list[can.Message]):
        self.channel_info = "queued"
        self.sent: list[can.Message] = []
        self._queued = list(queued)
        self.release = False
        super().__init__(channel="x")

    def send(self, msg, timeout=None):
        self.sent.append(msg)

    def _recv_internal(self, timeout=None):
        if self.release and self._queued:
            return self._queued.pop(0), False
        time.sleep(0.005)
        return None, False

    def shutdown(self):
        super().shutdown()  # see _FakeBus.shutdown


def _flow_control_frame(sent: list[can.Message]) -> can.Message | None:
    """The first ISO-TP Flow Control frame in ``sent`` (PCI high nibble 0x30)."""
    return next((m for m in sent if m.data and (m.data[0] & 0xF0) == 0x30), None)


def _addr(ecu: EcuAddress):
    return build_isotp_address(ecu)


class TestBuildIsotpStack:
    """Factory returns the FC-override subclass only when an fc_id is given."""

    def test_plain_stack_without_fc_id(self):
        bus = _FakeBus()
        notifier = can.Notifier(bus, [], timeout=0.05)
        try:
            st = build_isotp_stack(
                bus, notifier, _addr(EcuAddress(0x7E4, 0x7EC)), build_isotp_params(None)
            )
            assert isinstance(st, isotp.NotifierBasedCanStack)
            assert not isinstance(st, _FcAddressStack)
        finally:
            notifier.stop()
            bus.shutdown()

    def test_fc_override_stack_when_fc_id(self):
        bus = _FakeBus()
        notifier = can.Notifier(bus, [], timeout=0.05)
        addr = EcuAddress(0x18DB33F1, 0x18DAF1DB, AddressingMode.NORMAL_29BIT, fc_id=0x18DADBF1)
        try:
            st = build_isotp_stack(
                bus, notifier, _addr(addr), build_isotp_params(None), fc_id=addr.fc_id
            )
            assert isinstance(st, _FcAddressStack)
        finally:
            notifier.stop()
            bus.shutdown()


class TestFcAddressOverride:
    """_make_flow_control rewrites the FC frame to the physical fc_id (gap G-J)."""

    def _instance(self, fc_id: int) -> _FcAddressStack:
        # Build without the full NotifierBasedCanStack init (which spins a thread);
        # only the override's own attributes matter for this unit.
        inst = _FcAddressStack.__new__(_FcAddressStack)
        inst._fc_id = fc_id
        inst._fc_extended = fc_id > 0x7FF
        return inst

    def test_29bit_fc_redirected(self, monkeypatch):
        # Base emits FC on the functional TX id; the override must redirect it to
        # the ECU's physical request id (Renault 0x18DB33F1 -> 0x18DADBF1).
        monkeypatch.setattr(
            isotp.NotifierBasedCanStack,
            "_make_flow_control",
            lambda self, *a, **k: can.Message(
                arbitration_id=0x18DB33F1, is_extended_id=True, data=b"\x30\x00\x00"
            ),
        )
        inst = self._instance(0x18DADBF1)
        msg = inst._make_flow_control()
        assert msg.arbitration_id == 0x18DADBF1
        assert msg.is_extended_id is True

    def test_11bit_fc_id_not_extended(self, monkeypatch):
        monkeypatch.setattr(
            isotp.NotifierBasedCanStack,
            "_make_flow_control",
            lambda self, *a, **k: can.Message(
                arbitration_id=0x700, is_extended_id=False, data=b"\x30\x00\x00"
            ),
        )
        inst = self._instance(0x740)
        msg = inst._make_flow_control()
        assert msg.arbitration_id == 0x740
        assert msg.is_extended_id is False


class TestFcOverrideRealStack:
    """End-to-end: a real _FcAddressStack over a mock bus emits its Flow Control
    frame to the fc_id (gap G-J). This exercises the genuine can-isotp
    ``_make_flow_control`` / ``CanMessage`` (not a monkeypatched stand-in), so a
    library rename/restructure of that object is caught here rather than sliding
    past the unit test above.
    """

    def _drive_and_get_fc(self, addr: EcuAddress, ff: can.Message) -> can.Message | None:
        bus = _QueuedBus([ff])
        notifier = can.Notifier(bus, [], timeout=0.02)
        st = build_isotp_stack(
            bus, notifier, _addr(addr), build_isotp_params(None), fc_id=addr.fc_id
        )
        try:
            st.start()
            # Let start() register its listener before the FirstFrame is delivered,
            # so the Notifier thread doesn't drain+drop it first.
            time.sleep(0.1)
            bus.release = True
            deadline = time.monotonic() + 3.0
            fc = None
            while time.monotonic() < deadline:
                fc = _flow_control_frame(bus.sent)
                if fc is not None:
                    break
                time.sleep(0.02)
            return fc
        finally:
            st.stop()
            notifier.stop()
            bus.shutdown()

    def test_29bit_fc_redirected_to_physical_id(self):
        # Renault functional-TX 0x18DB33F1 / physical-RX 0x18DAF1DB: flow control
        # must go to the ECU's physical id 0x18DADBF1, not the functional TX id.
        addr = EcuAddress(0x18DB33F1, 0x18DAF1DB, AddressingMode.NORMAL_29BIT, fc_id=0x18DADBF1)
        # Multi-frame response FirstFrame (0x10, length 0x00A) on the RX id.
        ff = can.Message(
            arbitration_id=0x18DAF1DB,
            is_extended_id=True,
            data=bytes([0x10, 0x0A, 0x62, 0xBC, 0x03, 0x01, 0x02, 0x03]),
        )
        fc = self._drive_and_get_fc(addr, ff)
        assert fc is not None, "stack never emitted a Flow Control frame"
        assert fc.arbitration_id == 0x18DADBF1
        assert fc.is_extended_id is True

    def test_11bit_fc_redirected_not_extended(self):
        # An 11-bit fc_id keeps the standard-frame flag.
        addr = EcuAddress(0x7E4, 0x7EC, AddressingMode.NORMAL_11BIT, fc_id=0x740)
        ff = can.Message(
            arbitration_id=0x7EC,
            is_extended_id=False,
            data=bytes([0x10, 0x0A, 0x62, 0xBC, 0x03, 0x01, 0x02, 0x03]),
        )
        fc = self._drive_and_get_fc(addr, ff)
        assert fc is not None, "stack never emitted a Flow Control frame"
        assert fc.arbitration_id == 0x740
        assert fc.is_extended_id is False
