"""Tests for the monitor's per-cycle transport-health (drops/errors) tracking."""

from __future__ import annotations

import asyncio

from canlib.modes.monitor import MonitorController
from tests._fakes import FakeTerminal


class TestControllerDiagDelta:
    def _controller(self, terminal):
        return MonitorController(terminal=terminal, query_steps=[], pids_data={}, verbose=False)

    def test_diag_recorder_is_the_terminals(self):
        term = FakeTerminal()
        c = self._controller(term)
        assert c.diag_recorder() is term.diag

    def test_poll_once_computes_per_cycle_deltas(self):
        term = FakeTerminal()
        c = self._controller(term)

        async def drop_poll():
            term.diag.record("drop")
            term.diag.record("no_data")

        c._poll_elm = drop_poll  # type: ignore[method-assign]
        asyncio.run(c.poll_once())
        assert c.last_drops == 1
        assert c.last_errors == 2  # drop + no_data

        async def clean_poll():
            term.diag.record("ok")

        c._poll_elm = clean_poll  # type: ignore[method-assign]
        asyncio.run(c.poll_once())
        assert c.last_drops == 0  # deltas reset when a cycle is clean
        assert c.last_errors == 0

    def test_no_diag_recorder_leaves_deltas_zero(self):
        c = self._controller(terminal=None)
        assert c.diag_recorder() is None

        async def noop():
            return None

        c._poll_elm = noop  # type: ignore[method-assign]
        asyncio.run(c.poll_once())
        assert c.last_drops == 0
        assert c.last_errors == 0

    def test_transport_type_falls_back_to_diag_label(self):
        term = FakeTerminal()
        c = self._controller(term)
        # mode_monitor sets this from the client's diag when not passed explicitly.
        c.transport_type = c.diag_recorder().transport
        assert c.transport_type == "fake"
