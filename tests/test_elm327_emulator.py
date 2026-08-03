"""Opt-in end-to-end test of the elm327-tcp transport against ELM327-Emulator.

Proves canair's direct-ELM327 TCP path works against a real ELM327 wire (channel
+ engine + `>`-prompt framing over an actual socket), device-free. Skipped unless
the `elm` package (https://github.com/ircama/ELM327-emulator) is importable — it
ships a legacy build that needs `setuptools<80` (newer setuptools dropped
`pkg_resources`), so it is NOT a canair dev dependency. Install it manually to run
these:

    uv pip install "setuptools<80"
    uv pip install --no-build-isolation ELM327-emulator

The core test suite stays green without it.

Notes on the emulator's quirks (why these tests look the way they do):
- Its ``-n`` TCP mode serves a *single* client, then stops accepting — so the
  fixture is function-scoped (a fresh emulator per test).
- The ``0100`` PID simulates bus-init (``SEARCHING...`` + a 4.5 s sleep) and is
  unstable over TCP; we read stable stateless PIDs (``0105`` coolant, ``ATRV``
  voltage) instead. Multi-frame ISO-TP (VIN ``0902``) isn't reliably reassembled
  by the emulator over headers-off TCP, so it's exercised manually, not asserted.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest

pytest.importorskip("elm", reason="ELM327-emulator not installed (opt-in offline test)")

from canlib.transport.elm327_terminal import Elm327TcpTerminal


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


@pytest.fixture
def emulator():
    """Start ELM327-Emulator in TCP mode on an ephemeral port (single client)."""
    host, port = "127.0.0.1", _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "elm", "-n", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_port(host, port):
            proc.terminate()
            pytest.skip("ELM327-Emulator did not start listening in time")
        yield host, port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


async def _connect(host: str, port: int) -> Elm327TcpTerminal:
    term = Elm327TcpTerminal(host, port, timeout=6.0)
    await term.connect()
    # Disable echo (matches the init a real profile sends); ATSP0 auto-detects.
    await term.init_elm("ATE0;ATL0;ATSP0")
    return term


@pytest.mark.asyncio
async def test_at_command_roundtrip(emulator):
    """AT command round-trips over the real TCP socket — the core transport proof."""
    host, port = emulator
    term = await _connect(host, port)
    try:
        resp = await term.send_command("ATI")
        assert "ELM327" in resp
    finally:
        await term.close()


@pytest.mark.asyncio
async def test_obd_pid_and_voltage(emulator):
    """A stateless mode-01 PID parses as a positive UDS response; ATRV round-trips."""
    host, port = emulator
    term = await _connect(host, port)
    try:
        await term.set_header(0x7E0)
        # 0105 = engine coolant temperature; a clean single-frame answer (4105xx).
        resp = await term.send_uds("0105", timeout=6.0)
        assert resp["ok"] is True, resp
        assert resp["hex"].startswith("4105")
        # ATRV (battery voltage) exercises the send_command data path.
        assert "V" in await term.send_command("ATRV")
    finally:
        await term.close()
