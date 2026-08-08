"""Tests for canlib.transport.channel.WebSocketChannel — the primary WiCAN wire.

The ELM327 engine is transport-agnostic: it speaks to a ``Channel`` that owns the
transport-specific framing. ``TcpChannel`` (the ``elm327-tcp`` clone path) was well
covered while its WebSocket twin — the wire *every* WiCAN Pro session runs on —
had none: nothing anywhere referenced ``term_out``, ``ws_mode`` or
``WebSocketChannel``. This module covers the three things it uniquely owns:

* the ``{"ws_mode": "terminal", "terminal_type": "elm327"}`` connect handshake;
* unwrapping ``{"type": "term_out", "data": …}`` to the bare ELM text the engine
  expects, and swallowing control messages / binary frames as *no terminal text*
  rather than feeding them to the prompt accumulator;
* translating a websockets ``ConnectionClosed`` into ``ConnectionError`` — which
  is what the monitor's mid-session reconnect keys off (`modes/monitor.py` only
  recognises ``ConnectionError``).

Driven by a fake websocket connection, so no device and no real socket.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from canlib import config as canair_config
from canlib.transport import channel
from canlib.transport.channel import WebSocketChannel


class FakeWS:
    """Minimal stand-in for a websockets ClientConnection."""

    def __init__(self, incoming: list | None = None, *, closed_after: int | None = None):
        self.sent: list[str] = []
        self.closed = False
        self._incoming: list = list(incoming or [])
        self._recv_count = 0
        self._closed_after = closed_after

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self):
        if self._closed_after is not None and self._recv_count >= self._closed_after:
            raise websockets.exceptions.ConnectionClosedOK(None, None)
        self._recv_count += 1
        if self._incoming:
            return self._incoming.pop(0)
        await asyncio.sleep(3600)  # nothing pending: let the caller's timeout fire

    async def close(self) -> None:
        self.closed = True


def _channel(incoming=None, **kw) -> WebSocketChannel:
    ch = WebSocketChannel("10.0.2.86")
    ch.ws = FakeWS(incoming, **kw)  # type: ignore[assignment]
    return ch


class TestUrlAndLabel:
    def test_builds_the_terminal_url_from_the_host(self):
        assert WebSocketChannel("10.0.2.86").url == "ws://10.0.2.86/ws"

    def test_transport_name_labels_the_diagnostics_tally(self):
        # The engine tags each exchange with this; the recorded capture
        # provenance and the monitor's drops indicator read it.
        assert WebSocketChannel("h").transport_name == "wican-ws"


class TestConnectHandshake:
    @pytest.mark.asyncio
    async def test_sends_the_terminal_mode_handshake(self, monkeypatch):
        """The WiCAN only emits ELM text after this exact opt-in message."""
        fake = FakeWS()

        async def fake_connect(url, **kwargs):
            fake.url = url
            fake.kwargs = kwargs
            return fake

        monkeypatch.setattr(channel.websockets, "connect", fake_connect)
        monkeypatch.setattr(channel, "SETTLE_SECONDS", 0)

        ch = WebSocketChannel("10.0.2.86")
        await ch.connect()

        assert fake.url == "ws://10.0.2.86/ws"
        assert json.loads(fake.sent[0]) == {"ws_mode": "terminal", "terminal_type": "elm327"}

    @pytest.mark.asyncio
    async def test_connect_applies_the_shared_timeout_and_enables_pings(self, monkeypatch):
        fake = FakeWS()
        captured = {}

        async def fake_connect(url, **kwargs):
            captured.update(kwargs)
            return fake

        monkeypatch.setattr(channel.websockets, "connect", fake_connect)
        monkeypatch.setattr(channel, "SETTLE_SECONDS", 0)
        monkeypatch.setattr(canair_config, "ws_ping_interval", lambda: 20.0)
        await WebSocketChannel("h").connect()

        assert captured["open_timeout"] == channel.CONNECT_TIMEOUT
        # Pings are the only thing that notices a silently dead carrier (a NAT
        # rebind or a VPN re-key drops packets without a FIN), and the WiCAN's
        # /ws handler leaves PING to esp_http_server, so they never reach the
        # ELM327 reply framing.
        assert captured["ping_interval"] == 20.0
        assert captured["ping_timeout"] == 20.0

    @pytest.mark.asyncio
    async def test_ping_interval_is_configurable_and_disablable(self, monkeypatch):
        captured = {}

        async def fake_connect(url, **kwargs):
            captured.update(kwargs)
            return FakeWS()

        monkeypatch.setattr(channel.websockets, "connect", fake_connect)
        monkeypatch.setattr(channel, "SETTLE_SECONDS", 0)

        monkeypatch.setattr(canair_config, "ws_ping_interval", lambda: 5.0)
        await WebSocketChannel("h").connect()
        assert captured["ping_interval"] == 5.0

        monkeypatch.setattr(canair_config, "ws_ping_interval", lambda: None)
        await WebSocketChannel("h").connect()
        assert captured["ping_interval"] is None

    @pytest.mark.asyncio
    async def test_connect_drains_pending_traffic(self, monkeypatch):
        """Anything queued before we start must not become the first reply."""
        fake = FakeWS([json.dumps({"type": "term_out", "data": "stale\r>"})])

        async def fake_connect(url, **kwargs):
            return fake

        monkeypatch.setattr(channel.websockets, "connect", fake_connect)
        monkeypatch.setattr(channel, "SETTLE_SECONDS", 0)
        ch = WebSocketChannel("h")
        await ch.connect()
        # The queued message was consumed by connect()'s drain.
        assert fake._incoming == []


class TestRecvUnwrapping:
    @pytest.mark.asyncio
    async def test_unwraps_term_out_to_bare_elm_text(self):
        ch = _channel([json.dumps({"type": "term_out", "data": "41 0C 1A F8\r>"})])
        assert await ch.recv(1.0) == "41 0C 1A F8\r>"

    @pytest.mark.asyncio
    async def test_control_message_yields_no_terminal_text(self):
        """A ws_mode ack must not reach the engine's prompt accumulator.

        Returning its JSON as terminal text would inject a stray `>`-free blob
        into the reply being assembled.
        """
        ch = _channel([json.dumps({"ws_mode": "terminal", "status": "ok"})])
        assert await ch.recv(1.0) == ""

    @pytest.mark.asyncio
    async def test_binary_frame_yields_no_terminal_text(self):
        ch = _channel([b"\x01\x02"])
        assert await ch.recv(1.0) == ""

    @pytest.mark.asyncio
    async def test_non_json_text_passes_through(self):
        # A firmware that ever sends raw ELM text instead of the JSON envelope.
        ch = _channel(["41 0C 1A F8\r>"])
        assert await ch.recv(1.0) == "41 0C 1A F8\r>"

    @pytest.mark.asyncio
    async def test_non_dict_json_passes_through_as_text(self):
        # `json.loads("123")` succeeds but isn't an envelope; treat it as text
        # rather than calling .get() on an int (which used to raise).
        ch = _channel(["123"])
        assert await ch.recv(1.0) == "123"

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        # The engine reads None as "no data yet", not as an error.
        ch = _channel()
        assert await ch.recv(0.05) is None


class TestConnectionClosedTranslation:
    @pytest.mark.asyncio
    async def test_closed_socket_raises_connection_error(self):
        """`ConnectionClosed` is NOT an OSError, so it must be translated.

        The monitor's mid-session reconnect only recognises `ConnectionError`; an
        untranslated websockets exception escapes the poll loop and the session
        exits instead of re-homing.
        """
        ch = _channel(closed_after=0)
        with pytest.raises(ConnectionError, match="closed"):
            await ch.recv(1.0)

    def test_websockets_connection_closed_is_not_an_oserror(self):
        # Pins the premise of the translation above: if this ever became an
        # OSError subclass upstream, the translation would be redundant — but
        # while it isn't, removing it silently breaks reconnect.
        assert not issubclass(websockets.exceptions.ConnectionClosed, OSError)


class TestDrainAndClose:
    @pytest.mark.asyncio
    async def test_drain_consumes_pending_then_stops_on_quiet(self):
        ch = _channel(
            [
                json.dumps({"type": "term_out", "data": "a"}),
                json.dumps({"type": "term_out", "data": "b"}),
            ]
        )
        await ch.drain(per_recv_timeout=0.05, max_seconds=1.0)
        assert ch.ws._incoming == []

    @pytest.mark.asyncio
    async def test_drain_is_bounded_by_max_seconds(self):
        """A device streaming continuously must not hang the drain."""
        endless = _channel()
        endless.ws._incoming = [json.dumps({"type": "term_out", "data": "x"})] * 10_000
        started = asyncio.get_running_loop().time()
        await endless.drain(per_recv_timeout=0.01, max_seconds=0.2)
        assert asyncio.get_running_loop().time() - started < 2.0

    @pytest.mark.asyncio
    async def test_drain_without_a_connection_is_a_noop(self):
        ch = WebSocketChannel("h")
        await ch.drain()  # must not raise

    @pytest.mark.asyncio
    async def test_drain_surfaces_a_closed_socket(self):
        """A drain that hits a dead socket must not report success.

        The drain runs as the first half of a pipe resync; swallowing the close
        here made a dead link look like a line that had gone quiet, so the resync
        "succeeded" and the session kept polling a socket that would never answer.
        """
        ch = _channel(closed_after=0)
        with pytest.raises(ConnectionError, match="closed"):
            await ch.drain(per_recv_timeout=0.05, max_seconds=0.5)

    @pytest.mark.asyncio
    async def test_close_closes_and_clears_the_socket(self):
        ch = _channel()
        ws = ch.ws
        await ch.close()
        assert ws.closed
        assert ch.ws is None

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        ch = _channel()
        await ch.close()
        await ch.close()  # must not raise on the second call
