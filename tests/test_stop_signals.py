"""Tests for canlib.stop_signals — every "stop" signal must unwind gracefully.

A live session owns the device's single connection, so SIGTERM (a `kill`, or the
lock watchdog standing down) and SIGHUP (the controlling terminal went away —
an SSH disconnect, a closed window) must both reach the same graceful path as
Ctrl-C. SIGHUP's *default* disposition kills the process outright, skipping the
``--save`` journal reconcile, which is exactly what this guards against.
"""

import asyncio
import os
import signal

import pytest

from canlib.stop_signals import STOP_SIGNALS, graceful_stop, graceful_stop_async


class TestStopSignals:
    def test_covers_sigterm_and_sighup(self):
        assert set(STOP_SIGNALS) == {signal.SIGTERM, signal.SIGHUP}
        # SIGINT stays untouched: Python already raises KeyboardInterrupt, and
        # Textual binds Ctrl-C itself.
        assert signal.SIGINT not in STOP_SIGNALS


class TestGracefulStop:
    def test_installs_and_restores_each_signal(self):
        before = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}

        def _handler(_sig, _frame):
            pass

        with graceful_stop(_handler):
            for sig in STOP_SIGNALS:
                assert signal.getsignal(sig) is _handler
        for sig, prev in before.items():
            assert signal.getsignal(sig) == prev

    def test_restores_even_when_the_body_raises(self):
        before = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
        with pytest.raises(RuntimeError), graceful_stop(lambda _s, _f: None):
            raise RuntimeError("boom")
        for sig, prev in before.items():
            assert signal.getsignal(sig) == prev

    @pytest.mark.parametrize("sig", STOP_SIGNALS)
    def test_a_real_signal_reaches_the_handler(self, sig):
        seen: list[int] = []
        with graceful_stop(lambda s, _f: seen.append(s)):
            signal.raise_signal(sig)
        assert seen == [sig], f"{sig!r} never reached the graceful-stop handler"


class TestGracefulStopAsync:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("sig", STOP_SIGNALS)
    async def test_schedules_the_callback_on_the_loop(self, sig):
        """The Textual monitor path: a stop signal must not raise inside the loop."""
        fired = asyncio.Event()

        with graceful_stop_async(fired.set):
            os.kill(os.getpid(), sig)
            await asyncio.wait_for(fired.wait(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_restores_previous_handlers(self):
        before = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
        with graceful_stop_async(lambda: None):
            pass
        for sig, prev in before.items():
            assert signal.getsignal(sig) == prev
