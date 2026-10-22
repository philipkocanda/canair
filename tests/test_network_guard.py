"""The suite-wide guard that keeps tests off the network (``tests/conftest.py``).

A test that drives a live path without stubbing its probe used to open real
sockets to whatever host it resolved — the default WiCAN address included.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from tests.conftest import NetworkBlocked


def test_sync_connect_off_loopback_is_blocked_and_recorded(_no_real_sockets):
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("192.0.2.1", 80), timeout=0.1)
    with socket.socket() as sock:
        with pytest.raises(NetworkBlocked):
            sock.connect_ex(("192.0.2.1", 80))
    assert len(_no_real_sockets) == 2
    _no_real_sockets.clear()  # handled here, so teardown must not fail this test


def test_asyncio_connect_off_loopback_is_blocked(_no_real_sockets):
    async def go():
        await asyncio.open_connection("192.0.2.1", 35000)

    with pytest.raises(NetworkBlocked):
        asyncio.run(go())
    assert _no_real_sockets
    _no_real_sockets.clear()


def test_name_resolution_is_blocked(_no_real_sockets):
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("wican.example", 80)
    assert _no_real_sockets == ["getaddrinfo('wican.example')"]
    _no_real_sockets.clear()


def test_loopback_stays_open(_no_real_sockets):
    server = socket.create_server(("127.0.0.1", 0))
    try:
        with socket.create_connection(server.getsockname(), timeout=1.0):
            pass
    finally:
        server.close()
    assert _no_real_sockets == []
