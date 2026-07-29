"""Tests for canlib.transport.errors — centralized transport-error classification."""

import errno
import socket

from canlib.transport.errors import (
    connect_error_detail,
    describe_transport_error,
    is_transport_error,
    transport_error_types,
)


class TestConnectErrorDetail:
    def test_timeout(self):
        assert "timed out" in connect_error_detail(TimeoutError())

    def test_refused(self):
        assert "refused" in connect_error_detail(ConnectionRefusedError())

    def test_reset_is_a_drop(self):
        assert "dropped" in connect_error_detail(ConnectionResetError())

    def test_broken_pipe_is_a_drop(self):
        assert "dropped" in connect_error_detail(BrokenPipeError())

    def test_name_resolution(self):
        assert "resolution" in connect_error_detail(socket.gaierror("nope"))

    def test_no_route(self):
        assert (
            "no route"
            in connect_error_detail(OSError(errno.EHOSTUNREACH, "No route to host")).lower()
        )

    def test_network_unreachable(self):
        assert (
            "network is unreachable"
            in connect_error_detail(OSError(errno.ENETUNREACH, "Network is unreachable")).lower()
        )

    def test_unknown_falls_back_to_message(self):
        assert "weird" in connect_error_detail(OSError("weird failure"))


class TestTransportErrorTypes:
    def test_oserror_family_is_transport(self):
        assert is_transport_error(TimeoutError())
        assert is_transport_error(ConnectionResetError())
        assert is_transport_error(OSError("x"))

    def test_can_error_is_transport(self):
        import can

        assert is_transport_error(can.CanError("closed by peer"))

    def test_websocket_error_is_transport(self):
        import websockets

        assert is_transport_error(websockets.exceptions.WebSocketException())

    def test_value_error_is_not_transport(self):
        # A real bug must NOT be swallowed as a transport failure.
        assert not is_transport_error(ValueError("bug"))
        assert not is_transport_error(KeyError("bug"))

    def test_types_are_cached(self):
        assert transport_error_types() is transport_error_types()


class TestDescribeTransportError:
    def test_names_transport_and_reason(self):
        msg = describe_transport_error(TimeoutError(), host="10.0.2.86", transport_label="SLCAN")
        assert "SLCAN" in msg
        assert "10.0.2.86" in msg
        assert "timed out" in msg

    def test_recover_hint_only_when_saving(self):
        drop = ConnectionResetError()
        assert "--recover" not in describe_transport_error(
            drop, host="h", transport_label="SLCAN", saving=False
        )
        assert "--recover" in describe_transport_error(
            drop, host="h", transport_label="SLCAN", saving=True
        )

    def test_non_oserror_uses_its_own_message(self):
        import can

        msg = describe_transport_error(
            can.CanError("connection closed by peer"),
            host="h",
            transport_label="SLCAN",
        )
        assert "closed by peer" in msg

    def test_no_host_omits_diagnose_line(self):
        msg = describe_transport_error(TimeoutError(), host=None, transport_label="SLCAN")
        assert "canair status" not in msg
