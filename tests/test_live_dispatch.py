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
