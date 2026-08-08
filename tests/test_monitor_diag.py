"""Tests for the monitor's per-cycle transport-health (drops/errors) tracking."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

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

        c._poll_elm = stale_poll  # type: ignore[method-assign]
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


class TestHealthLineSegments:
    """The TUI health line must not fold a desync into the drop counter.

    `_health_items` reads only `self.controller` and `self._last_stale()`, so it is
    exercised against a stub rather than a mounted Textual app.
    """

    @staticmethod
    def _items(*, drops: int, stale: int, errors: int, resyncs: int, last_stale: int = 0):
        from canlib.modes._monitor_tui import MonitorApp

        diag = SimpleNamespace(drops=drops, stale=stale, errors=errors, resyncs=resyncs)
        stub = SimpleNamespace(
            controller=SimpleNamespace(diag=lambda: diag, last_drops=drops),
            _last_stale=lambda: last_stale,
        )
        return [i.markup for i in MonitorApp._health_items(stub)]  # type: ignore[arg-type]

    def test_stale_is_its_own_segment(self):
        texts = self._items(drops=3, stale=2, errors=5, resyncs=0)
        joined = " ".join(texts)
        assert "stale" in joined and "drops" in joined
        # drops shown net of stale (3 total - 2 stale = 1), so the two don't
        # double-count the same exchanges.
        assert "[b red]1[/]" in texts[0]
        assert "[b red]2[/]" in texts[1]

    def test_resyncs_are_reported_even_though_they_are_recoveries(self):
        # A link that keeps needing realignment is degrading; hiding a successful
        # recovery makes the run look healthy right up to the moment it isn't.
        texts = self._items(drops=0, stale=1, errors=1, resyncs=4)
        assert any("resync" in t and "4" in t for t in texts)

    def test_clean_run_shows_nothing(self):
        assert self._items(drops=0, stale=0, errors=0, resyncs=0) == []

    def test_no_diag_recorder_is_empty(self):
        from canlib.modes._monitor_tui import MonitorApp

        stub = SimpleNamespace(controller=SimpleNamespace(), _last_stale=lambda: 0)
        assert MonitorApp._health_items(stub) == []  # type: ignore[arg-type]
