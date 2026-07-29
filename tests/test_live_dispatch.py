"""Transport guards in the shared live dispatcher (canlib/commands/_live.py).

``dispatch_mode`` is typed against the :class:`~canlib.transport.protocol.Terminal`
contract, so WiCAN-only modes must narrow explicitly. The interactive REPL sets
the ECU header via raw ``ATSH`` sends, which are no-ops on the raw transport
(RawTerminal uses ``set_header()``), so a UDS request there would fire with no
header set — dispatch refuses it with a clear error rather than a broken prompt,
mirroring the skm-wake guard.
"""

import argparse

import pytest


class TestInteractiveTransportGuard:
    @pytest.mark.asyncio
    async def test_interactive_refused_on_non_elm_terminal(self, capsys):
        from canlib.commands._live import CANAIR_DEFAULTS, dispatch_mode

        class NotWiCAN:  # not a WiCANTerminal -> raw path; no mode selector set
            pass

        # No mode selector => the else (interactive) branch.
        args = argparse.Namespace(**CANAIR_DEFAULTS)
        with pytest.raises(SystemExit):
            await dispatch_mode(args, NotWiCAN(), {}, "1.2.3.4")
        assert "wican-ws" in capsys.readouterr().err


class _FakeLock:
    def acquire(self, force=False):
        pass

    def release(self):
        pass


class TestRunLiveSigterm:
    def test_maps_sigterm_to_graceful_and_restores(self, monkeypatch):
        import signal

        from canlib.commands import _live

        monkeypatch.setattr(_live, "WiCANLock", lambda *a, **k: _FakeLock())

        captured = {}

        async def fake_main(args):
            # The SIGTERM handler active mid-run should raise KeyboardInterrupt
            # (the same graceful path as Ctrl-C), so a `kill`/`pkill` unwinds.
            captured["handler"] = signal.getsignal(signal.SIGTERM)
            return 0

        monkeypatch.setattr(_live, "async_main", fake_main)

        before = signal.getsignal(signal.SIGTERM)
        rc = _live.run_live(argparse.Namespace(force=False))
        assert rc == 0
        with pytest.raises(KeyboardInterrupt):
            captured["handler"](signal.SIGTERM, None)
        # The previous handler is restored on the way out.
        assert signal.getsignal(signal.SIGTERM) == before

    def test_keyboardinterrupt_exits_cleanly(self, monkeypatch, capsys):
        from canlib.commands import _live

        monkeypatch.setattr(_live, "WiCANLock", lambda *a, **k: _FakeLock())

        async def fake_main(args):
            raise KeyboardInterrupt

        monkeypatch.setattr(_live, "async_main", fake_main)
        rc = _live.run_live(argparse.Namespace(force=False))
        assert rc == 0
        assert "Interrupted" in capsys.readouterr().out
