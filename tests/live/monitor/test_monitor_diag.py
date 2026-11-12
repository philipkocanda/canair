"""Tests for the monitor's per-cycle transport-health (drops/errors) tracking."""

from __future__ import annotations

import asyncio

from canair.live.monitor.controller import MonitorController
from tests.support.fakes import FakeTerminal
from tests.support.monitor_controller import make_controller


class TestControllerDiagDelta:
    def _controller(self, terminal) -> MonitorController:
        return make_controller(terminal=terminal, query_steps=[], pids_data={}, verbose=False)

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

        c.elm_poller.poll = drop_poll  # type: ignore[method-assign]
        asyncio.run(c.poll_once())
        assert c.last_drops == 1
        assert c.last_errors == 2  # drop + no_data

        async def clean_poll():
            term.diag.record("ok")

        c.elm_poller.poll = clean_poll  # type: ignore[method-assign]
        asyncio.run(c.poll_once())
        assert c.last_drops == 0  # deltas reset when a cycle is clean
        assert c.last_errors == 0

    def test_no_diag_recorder_leaves_deltas_zero(self):
        c = self._controller(terminal=None)
        assert c.diag_recorder() is None

        async def noop():
            return None

        c.elm_poller.poll = noop  # type: ignore[method-assign]
        asyncio.run(c.poll_once())
        assert c.last_drops == 0
        assert c.last_errors == 0

    def test_poll_once_tracks_stale_apart_from_drops(self):
        """`stale` needs its own per-cycle delta.

        `TransportStats.drops` deliberately sums drop+stale for capture-quality
        back-compat, which makes it useless for telling a corrupt-reassembly burst
        apart from a desynchronised pipe — the two need different responses.
        """
        term = FakeTerminal()
        c = self._controller(term)

        async def stale_poll():
            term.diag.record("stale")

        c.elm_poller.poll = stale_poll  # type: ignore[method-assign]
        asyncio.run(c.poll_once())
        assert c.last_stale == 1
        assert c.last_drops == 1  # drops still conflates, hence last_stale
        assert c.last_errors == 1

    def test_transport_type_falls_back_to_diag_label(self):
        term = FakeTerminal()
        c = self._controller(term)
        # mode_monitor sets this from the client's diag when not passed explicitly.
        c.transport_type = c.diag_recorder().transport
        assert c.transport_type == "fake"
