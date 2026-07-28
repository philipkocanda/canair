"""Conformance tests for the ``Terminal`` protocol (canlib/transport/protocol.py).

The dual-transport surface is the "most important architectural rule": modes are
written against :class:`~canlib.transport.protocol.Terminal`, never a concrete
class, so a new backend slots in by structurally satisfying it. These runtime
``isinstance`` smoke checks guard that both real terminals *and* the shared test
fake keep implementing the surface — the regression guard against signature
drift (the real oracle for method *shapes* is ``ty check`` over the retyped
seam). The fake's conformance is what would have caught the divergent
``send_uds`` signatures the shared-fake work consolidated.
"""

import pytest

from canlib.terminal import WiCANTerminal
from canlib.transport import Terminal, raw_terminal
from canlib.transport import slcan_tcp as slcan_mod
from tests._fakes import FakeTerminal


class _FakeBus:
    def __init__(self, *a, **k):
        pass

    def shutdown(self):
        pass


class _FakeNotifier:
    def __init__(self, *a, **k):
        pass

    def stop(self):
        pass


def test_wican_terminal_satisfies_protocol():
    # Constructor does not connect (ws stays None), so this is device-free.
    assert isinstance(WiCANTerminal(host="10.0.2.86"), Terminal)


def test_raw_terminal_satisfies_protocol(monkeypatch):
    # RawTerminal opens the bus/notifier in __init__; stub them out.
    monkeypatch.setattr(slcan_mod, "SlcanTcpBus", _FakeBus)
    monkeypatch.setattr(raw_terminal.can, "Notifier", _FakeNotifier)
    assert isinstance(raw_terminal.RawTerminal("h", 3333, 500000), Terminal)


def test_fake_terminal_satisfies_protocol():
    assert isinstance(FakeTerminal(), Terminal)


def test_bare_object_is_not_a_terminal():
    # Sanity: the protocol is not vacuously satisfied by everything.
    assert not isinstance(object(), Terminal)


@pytest.mark.parametrize(
    "method",
    ["set_header", "send_uds", "send_command", "enter_extended_session", "close"],
)
def test_protocol_lists_the_dual_transport_surface(method):
    # The surface the contributing skill names — asserted here so a rename in the
    # protocol without updating the real terminals is caught.
    assert hasattr(WiCANTerminal(host="10.0.2.86"), method)
