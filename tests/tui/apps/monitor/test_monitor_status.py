"""What the monitor's bars say: the zones, the menu bar's title, the bottom line, the menus.

The functions in ``canair.tui.apps.monitor.status`` read only the controller, so they are
exercised against stubs rather than a mounted app.
"""

from __future__ import annotations

from types import SimpleNamespace

from rich.text import Text

from canair.tui.apps.monitor.commands import monitor_catalogue
from canair.tui.apps.monitor.status import (
    car_zone,
    context_items,
    document_items,
    health_items,
    link_items,
    link_zone,
    poll_zone,
    record_zone,
    session_word,
)
from tests.support.monitor_tui import FakeController, FakeEditor


def _plain(items) -> str:
    return " ".join(Text.from_markup(i.markup).plain for i in items)


class TestHealthItems:
    """The link zone must not fold a desync into the drop counter."""

    @staticmethod
    def _items(*, drops: int, stale: int, errors: int, resyncs: int, last_stale: int = 0):
        diag = SimpleNamespace(drops=drops, stale=stale, errors=errors, resyncs=resyncs)
        controller = SimpleNamespace(diag=lambda: diag, last_drops=drops, last_stale=last_stale)
        return health_items(controller)

    def test_stale_is_its_own_badge_and_drops_are_net_of_it(self):
        items = self._items(drops=3, stale=2, errors=5, resyncs=0)
        # 3 total - 2 stale = 1 drop, so the two never double-count the same exchanges.
        assert Text.from_markup(items[0].markup).plain.strip().startswith("✗ 1 drops")
        assert Text.from_markup(items[1].markup).plain.strip().startswith("✗ 2 stale")
        assert "reverse" in items[0].markup  # a data-integrity fault is a badge

    def test_resyncs_are_reported_even_though_they_are_recoveries(self):
        # A link that keeps needing realignment is degrading; hiding a successful
        # recovery makes the run look healthy right up to the moment it isn't.
        assert "⚠ 4 resync" in _plain(self._items(drops=0, stale=1, errors=1, resyncs=4))

    def test_clean_run_shows_nothing(self):
        assert self._items(drops=0, stale=0, errors=0, resyncs=0) == []

    def test_no_diag_recorder_is_empty(self):
        assert health_items(SimpleNamespace()) == []


class TestLinkLatency:
    """On a remote link, "the car is quiet" and "the network is slow" look alike."""

    @staticmethod
    def _controller(rtt):
        from canair.transport.link_latency import LinkLatency

        link = LinkLatency()
        if rtt is not None:
            for _ in range(10):
                link.observe(rtt)
        return SimpleNamespace(link=lambda: link)

    def test_an_unmeasured_link_shows_nothing(self):
        assert link_items(self._controller(None)) == []

    def test_a_lan_round_trip_is_not_worth_a_column(self):
        # Sub-50ms is not what limits a poll cycle, so it is noise on the line.
        assert link_items(self._controller(0.001)) == []

    def test_a_cellular_round_trip_is_reported(self):
        assert "rtt 800ms" in _plain(link_items(self._controller(0.8)))

    def test_a_controller_without_a_link_is_tolerated(self):
        assert link_items(SimpleNamespace(link=lambda: None)) == []
        assert link_items(SimpleNamespace()) == []


class TestZones:
    def test_a_healthy_link_says_so_and_names_the_device(self):
        c = SimpleNamespace(device_host=lambda: "192.0.2.10", transport_type="wican-ws")
        text = _plain(link_zone(c).items)
        assert text.startswith("✓ link") and "192.0.2.10" in text and "wican-ws" in text

    def test_a_faulty_link_drops_the_ok_and_leads_with_the_fault(self):
        diag = SimpleNamespace(drops=2, stale=0, errors=2, resyncs=0)
        c = SimpleNamespace(diag=lambda: diag, last_drops=0, last_stale=0)
        text = _plain(link_zone(c).items)
        assert "✓ link" not in text and "✗ 2 drops" in text

    def test_a_reconnect_is_a_badge_with_its_note(self):
        c = SimpleNamespace(reconnecting=True, reconnect_note="probing 192.0.2.10")
        text = _plain(link_zone(c).items)
        assert "⟳ RECONNECTING" in text and "probing" in text and "✓ link" not in text

    def test_poll_says_live_or_a_paused_badge(self):
        c = FakeController()
        assert _plain(poll_zone(c, paused=False).items).startswith("LIVE #0")
        paused = poll_zone(c, paused=True).items[0]
        assert "reverse" in paused.markup and "PAUSED" in paused.markup

    def test_recording_shows_rec_only_when_journaling(self):
        assert "REC" in _plain(record_zone(FakeController(journal=object())).items)
        assert "not recording" in _plain(record_zone(FakeController()).items)

    def test_the_car_names_its_sessions_and_an_output_left_on(self):
        c = FakeController()
        c.sessions.active_modes = lambda: {"BMS": "81", "VCU": "03"}
        c.actuation = SimpleNamespace(on_count=lambda: 1)
        items = car_zone(c).items
        assert "⚠ 1 ON" in Text.from_markup(items[0].markup).plain
        assert "BMS 81 standard" in _plain(items) and "VCU 03 extended" in _plain(items)

    def test_a_session_type_carries_its_name(self):
        assert session_word("81") == "81 standard"
        assert session_word("03") == "03 extended"
        assert session_word("7E") == "7E"  # not named: the hex alone, never a guess
        assert session_word("zz") == "zz"


class TestTitleAndContext:
    def test_the_title_is_the_label_then_states_notes_and_profile(self):
        c = FakeController()
        c.recorder.session_label = "test drive"
        c.recorder.session_states = ["READY"]
        c.recorder.session_notes = "heater on"
        c.profile = SimpleNamespace(name="hyundai-ioniq-ae-ev")
        # The menu bar joins the items with separators of its own.
        assert _plain(document_items(c)) == "test drive READY heater on hyundai-ioniq-ae-ev"

    def test_with_nothing_selected_the_context_teaches_the_picker(self):
        text = _plain(context_items(FakeController(editor=FakeEditor())))
        assert "↑/↓ select" in text and "p select ECUs/PIDs" in text

    def test_a_selection_shows_with_its_keys(self):
        ed = FakeEditor()
        ed.move([], 1)
        text = _plain(context_items(FakeController(editor=ed)))
        assert "▶ BMS 2101 SOC" in text and "e edit" in text and "v verify" in text

    def test_a_mode_that_is_not_the_default_shows(self):
        c = FakeController(editor=FakeEditor())
        c.view_mode = "ranges"
        c.editor.filter_mode = "verified"
        c.show_rulers = True
        text = _plain(context_items(c))
        assert "view ranges" in text and "filter verified" in text and "ruler" in text


class TestCatalogue:
    def test_every_command_sits_in_one_menu_and_names_an_app_action(self):
        from canair.tui.apps.monitor.app import MonitorApp

        cat = monitor_catalogue(recording=True, actuate=True)
        titles = [m.title for m in cat.menus]
        assert titles == ["Record", "Poll", "View", "Signal", "Actuators", "Help"]
        for _menu, cmd in cat.commands():
            name = cmd.action.split("(")[0]
            assert hasattr(MonitorApp, f"action_{name}"), cmd.id

    def test_the_menus_follow_the_run(self):
        plain = monitor_catalogue(recording=False, actuate=False)
        assert [m.title for m in plain.menus] == ["Record", "Poll", "View", "Signal", "Help"]
        assert plain["label"].label == "Save snapshot…"
        assert "actuators" not in plain
        assert monitor_catalogue(recording=True, actuate=False)["label"].label.startswith("Label")
