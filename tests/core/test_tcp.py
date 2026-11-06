import socket

from canair.core.tcp import tcp_open, tcp_probe


def _listening() -> socket.socket:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    return server


def test_an_open_port_answers():
    with _listening() as server:
        port = server.getsockname()[1]
        assert tcp_open("127.0.0.1", port, 1.0)
        assert tcp_probe("127.0.0.1", port, 1.0) is None


def test_a_closed_port_returns_the_reason():
    with _listening() as server:
        port = server.getsockname()[1]
    assert not tcp_open("127.0.0.1", port, 1.0)
    assert isinstance(tcp_probe("127.0.0.1", port, 1.0), OSError)
