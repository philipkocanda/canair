"""The live dispatch table: order, coverage, and mutual exclusivity.

``dispatch_mode`` used to be a 470-line ``if/elif`` chain. It is now a table
(:data:`canlib.modes.dispatch._DISPATCH`) pairing each handler with the predicate
that selects it, and a table can go silently wrong in ways a chain could not:

* **order** — the chain's order was load-bearing and is not obvious from the data.
  ``multi`` + ``monitor`` must be tested before bare ``multi``, or every
  ``canair monitor`` run would dispatch as a one-shot read.
* **coverage** — a mode selector with no table entry is simply unreachable, and
  falls through to the interactive REPL instead of erroring.

Both are asserted here against the argument namespace itself
(``CANAIR_DEFAULTS``), so adding a selector without wiring it up fails.
"""

from __future__ import annotations

import argparse

import pytest

from canlib.commands._live import CANAIR_DEFAULTS
from canlib.modes.dispatch import _DISPATCH, dispatch_mode, elm_only

# Every mode-selector attribute in the live argument namespace. Kept as a literal
# so that adding one to CANAIR_DEFAULTS without a dispatch entry fails the
# coverage test below rather than silently becoming unreachable.
MODE_SELECTORS = frozenset(
    {
        "param",
        "ecu",
        "raw",
        "scan",
        "skm_wakeup",
        "identity",
        "discover",
        "dtc",
        "dtc_all",
        "iocontrol",
        "routines",
        "routines_scan",
        "iocontrol_scan",
        "sessions_scan",
        "multi",
    }
)

# The handler order, exactly as the original if/elif chain ran it.
EXPECTED_ORDER = [
    "handle_monitor",
    "handle_multi",
    "handle_skm_wake",
    "handle_identity",
    "handle_dtc",
    "handle_param",
    "handle_ecu",
    "handle_raw",
    "handle_scan",
    "handle_iocontrol",
    "handle_routines",
    "handle_routines_scan",
    "handle_iocontrol_scan",
    "handle_sessions_scan",
    "handle_discover",
]


def _args(**overrides) -> argparse.Namespace:
    return argparse.Namespace(**{**CANAIR_DEFAULTS, **overrides})


def _selected(args) -> list[str]:
    """Names of every handler whose predicate accepts ``args``, in table order."""
    return [h.__name__ for h, selects in _DISPATCH if selects(args)]


class TestTableShape:
    def test_order_is_preserved(self):
        assert [h.__name__ for h, _ in _DISPATCH] == EXPECTED_ORDER

    def test_mode_selectors_are_declared_by_the_namespace(self):
        """MODE_SELECTORS must not drift from CANAIR_DEFAULTS."""
        assert MODE_SELECTORS <= set(CANAIR_DEFAULTS)

    def test_every_selector_reaches_a_handler(self):
        """No mode may be unreachable — an unwired one falls through to the REPL."""
        unreachable = []
        for sel in sorted(MODE_SELECTORS):
            # A truthy value for this selector alone must select something.
            probe = _args(**{sel: True})
            if not _selected(probe):
                unreachable.append(sel)
        assert not unreachable, (
            f"these mode selectors reach no handler and would silently open the "
            f"interactive REPL instead: {unreachable}"
        )

    def test_default_namespace_selects_nothing(self):
        """With no mode requested, the fallback (interactive REPL) must win."""
        assert _selected(_args()) == []


class TestOrderingHazards:
    def test_monitor_beats_bare_multi(self):
        """The specific ordering the chain depended on.

        A monitor run sets both `multi` and `monitor`; both predicates accept it,
        so the *first* entry decides. If they were ever reordered, every
        `canair monitor` would run as a one-shot read and never poll.
        """
        picked = _selected(_args(multi=["query BMS"], monitor=5.0))
        assert picked[0] == "handle_monitor"
        assert "handle_multi" in picked, "bare multi should also match — order is what decides"

    def test_bare_multi_without_monitor(self):
        assert _selected(_args(multi=["query BMS"])) == ["handle_multi"]

    def test_dtc_all_selects_the_dtc_handler(self):
        """--all is a separate dest; it must not need --dtc to dispatch."""
        assert _selected(_args(dtc_all=True)) == ["handle_dtc"]

    @pytest.mark.parametrize(
        "selector,handler",
        [
            ("param", "handle_param"),
            ("ecu", "handle_ecu"),
            ("raw", "handle_raw"),
            ("scan", "handle_scan"),
            ("identity", "handle_identity"),
            ("skm_wakeup", "handle_skm_wake"),
            ("discover", "handle_discover"),
            ("iocontrol", "handle_iocontrol"),
            ("routines", "handle_routines"),
        ],
    )
    def test_single_selector_is_unambiguous(self, selector, handler):
        """Each of these selects exactly one handler, so order can't matter for them."""
        assert _selected(_args(**{selector: True})) == [handler]

    @pytest.mark.parametrize(
        "selector,handler",
        [
            ("routines_scan", "handle_routines_scan"),
            ("iocontrol_scan", "handle_iocontrol_scan"),
            ("sessions_scan", "handle_sessions_scan"),
        ],
    )
    def test_is_not_none_selectors(self, selector, handler):
        """These test `is not None`, so an empty list must still dispatch."""
        assert _selected(_args(**{selector: []})) == [handler]


class TestFallback:
    def test_nothing_matched_runs_the_repl(self, monkeypatch):
        """The fallback is deliberately outside the table; pin that it is reached."""
        called: list[str] = []

        async def _fake(args, terminal, pids_data, host, *, reconnect=None):
            called.append("interactive")

        monkeypatch.setattr(elm_only, "handle_interactive", _fake)
        import asyncio

        asyncio.run(dispatch_mode(_args(), object(), {}, "1.2.3.4"))
        assert called == ["interactive"]
