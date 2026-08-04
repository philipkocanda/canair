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


def test_elm327_tcp_terminal_satisfies_protocol():
    # The third transport. It was absent from this module entirely, which is how
    # it shipped with a connect() missing the timeout/settle/drain its WebSocket
    # twin had — isinstance only checks method *presence*, never signatures.
    from canlib.transport.elm327_terminal import Elm327TcpTerminal

    assert isinstance(Elm327TcpTerminal("h", 35000), Terminal)


@pytest.mark.parametrize(
    "backend",
    [
        lambda: WiCANTerminal(host="10.0.2.86"),
        lambda: __import__(
            "canlib.transport.elm327_terminal", fromlist=["Elm327TcpTerminal"]
        ).Elm327TcpTerminal("h", 35000),
    ],
    ids=["wican-ws", "elm327-tcp"],
)
@pytest.mark.parametrize(
    "method",
    ["set_header", "send_uds", "send_command", "enter_extended_session", "close"],
)
def test_every_elm_backend_exposes_the_shared_surface(backend, method):
    """Both ELM transports must expose the whole surface, not just one of them.

    This previously checked only `WiCANTerminal`, despite its name claiming to
    cover the transport surface.
    """
    assert hasattr(backend(), method)


def test_static_conformance_module_is_side_effect_free():
    """`canlib/transport/_conformance.py` is a type-level check only.

    It binds every backend to its protocol so `ty` rejects a drifted signature
    (which `isinstance` cannot see). Importing it must stay harmless — no
    connections, no config reads — and its function must never be called.
    """
    from canlib.transport import _conformance

    assert callable(_conformance._static_conformance)
