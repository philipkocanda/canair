"""The monitor app quits through its own path on a stop signal."""

from __future__ import annotations

import asyncio
import signal

import pytest

from canair.core.stop_signals import STOP_SIGNALS
from canair.tui.apps import monitor as monitor_app


class TestTuiStop:
    """The TUI must exit through its own quit path, not a KeyboardInterrupt
    unwinding through Textual's internals (which leaves the terminal wrecked)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sig", STOP_SIGNALS)
    async def test_stop_signal_quits_the_app(self, monkeypatch, sig):
        class _FakeApp:
            def __init__(self, controller):
                self.controller = controller
                self._stopping = False
                self._exited = asyncio.Event()

            async def run_async(self):
                await self._exited.wait()

            def exit(self):
                self._exited.set()

        built: dict[str, _FakeApp] = {}

        def _build(controller):
            built["app"] = _FakeApp(controller)
            return built["app"]

        monkeypatch.setattr(monitor_app, "MonitorApp", _build)

        task = asyncio.create_task(monitor_app.run_monitor_app(object()))
        await asyncio.sleep(0.05)
        signal.raise_signal(sig)
        await asyncio.wait_for(task, timeout=5.0)

        # _stopping also aborts an in-flight reconnect, so --wait can't hold us.
        assert built["app"]._stopping
