"""Tests for the shared ISO-TP stack factory (canlib.transport.isotp_stack).

Covers the flow-control arbitration-id override (gap G-J: functional-TX /
physical-RX ECUs like Renault/Mitsubishi). See
plans/2026-07-28-multi-vehicle-support.md.
"""

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
        pass


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
