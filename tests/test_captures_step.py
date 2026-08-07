"""Tests for the ``captures --step`` viewer: renderer, model, TUI, entry point.

The join primitive itself is covered in ``test_captures.py``
(``TestBuildJoinFrames``/``TestNearestWithin``); here we cover what the user
sees and touches.
"""

from __future__ import annotations

import io
import json

import pytest
from rich.console import Console
from textual.containers import VerticalScroll

from canlib.commands.captures.step import cmd_step
from canlib.commands.captures.step_model import (
    AUTO_STACK_MAX_KEYS,
    BLOCK_NO_FRAME,
    BLOCK_NON_PAYLOAD,
    DEFAULT_STEP_JOIN_TOL_S,
    TOL_LADDER,
    VIEW_AUTO,
    VIEW_CHANGED,
    VIEW_INTERLEAVED,
    VIEW_SIGNALS,
    VIEW_STACKED,
    StepModel,
    resolve_view,
)
from canlib.commands.captures.step_render import capture_block_text

# Two params over the sample payload "6201005A6414" (a 6-byte single-frame
# response, so WiCAN Bnn maps to payload byte nn-1):
#   B04 -> payload byte 3 (0x5A, static); B06 -> payload byte 5 (0x14/0x15, moves)
PARAMS = {
    "P_STATIC": {"expression": "B04", "unit": "", "verified": True},
    "P_MOVING": {"expression": "B06", "unit": "A", "verified": False},
}


def _entry(**kw) -> dict:
    """A flat capture entry as ``load_all_captures`` would produce."""
    base = {
        "file": "2026-08-02.json",
        "date": "2026-08-02",
        "session_label": "",
        "vehicle_states": [],
        "session_notes": "",
        "ecu": "HVAC",
        "ecu_addr": "0x7BB",
        "pid": "220100",
        "payload": "6201005A6414",
        "response": None,
        "scan_results": None,
        "notes": "",
        "time": "12:00:00",
        "label": "",
        "_session_idx": 0,
        "_capture_idx": 0,
    }
    base.update(kw)
    return base


def _render(text) -> str:
    buf = io.StringIO()
    Console(file=buf, highlight=False, width=120).print(text, end="", soft_wrap=True)
    return buf.getvalue()


def _block(**kw) -> str:
    caps = [
        _entry(time="12:00:00", payload="6201005A6414"),
        _entry(time="12:00:05", payload="6201005A6415", label="ac-on", notes="compressor on"),
    ]
    defs = {("HVAC", "220100"): (PARAMS, 0x7B3)}
    kw.setdefault("position", "capture 2/2")
    return _render(capture_block_text(caps, 1, defs, [None, 0], [(1, 2), (2, 2)], **kw))


class TestCaptureBlockText:
    def test_renders_header_params_and_hex(self):
        out = _block(rulers=True, aliases={"HVAC": "DATC"})
        assert "HVAC (0x7B3)" in out
        assert "(alias DATC)" in out
        assert "220100" in out
        assert "12:00:05" in out
        assert "P_STATIC" in out and "P_MOVING" in out
        assert "62 01 00 5A 64 15" in out  # current payload
        assert "62 01 00 5A 64 14" in out  # previous, for the diff
        assert "wican" in out  # ruler row

    def test_capture_label_is_shown(self):
        """Regression: the label used to be eaten as Rich markup and vanish."""
        assert "[ac-on]" in _block()

    def test_capture_note_is_shown(self):
        assert "compressor on" in _block()

    def test_show_hex_false_drops_hex_and_ruler(self):
        out = _block(show_hex=False, rulers=True)
        assert "P_STATIC" in out
        assert "62 01 00" not in out
        assert "wican" not in out

    def test_changed_only_keeps_params_whose_value_moved(self):
        out = _block(changed_only=True)
        # Byte 5 moved 0x14 -> 0x15, so only the param reading it survives.
        assert "P_MOVING" in out
        assert "P_STATIC" not in out

    def test_changed_only_reports_when_nothing_moved(self):
        caps = [
            _entry(time="12:00:00", payload="6201005A6414"),
            _entry(time="12:00:05", payload="6201005A6414"),
        ]
        defs = {("HVAC", "220100"): (PARAMS, 0x7B3)}
        out = _render(
            capture_block_text(caps, 1, defs, [None, 0], [(1, 2), (2, 2)], changed_only=True)
        )
        assert "no signal changes" in out

    def test_cursor_and_delta_label(self):
        out = _block(selected=True, dt_label="Δt=+1.24s")
        assert "▶ HVAC" in out
        assert "Δt=+1.24s" in out
        assert "▶ HVAC" not in _block(selected=False)

    def test_per_pid_ordinal_can_be_suppressed(self):
        assert "this PID 2/2" in _block(show_per_pid=True)
        assert "this PID" not in _block(show_per_pid=False)

    def test_undefined_pid_renders_raw_hex(self):
        caps = [_entry(payload="6201005A6414")]
        out = _render(capture_block_text(caps, 0, {}, [None], [(1, 1)]))
        assert "62 01 00 5A 64 14" in out


def _model(*, keys=None, entries=None, **kw) -> StepModel:
    entries = entries if entries is not None else _three_pid_entries()
    keys = keys or sorted({(e["ecu"], str(e["pid"])) for e in entries})
    defs = dict.fromkeys(keys, (PARAMS, 0x7B3))
    kw.setdefault("captures_dir", None)
    return StepModel.from_entries(entries, keys, defs, **kw)


def _three_pid_entries() -> list[dict]:
    """Three HVAC PIDs polled round-robin over two cycles ~1s apart."""
    out = []
    for cycle, base in enumerate(("12:00:0", "12:01:0")):
        for n, pid in enumerate(("220100", "2201A0", "2201A2")):
            out.append(
                _entry(
                    pid=pid,
                    time=f"{base}{n}",
                    payload=f"6201005A64{cycle}{n}",
                    _capture_idx=cycle * 3 + n,
                )
            )
    return out


class TestJoinToleranceDefault:
    def test_is_ten_seconds(self):
        assert DEFAULT_STEP_JOIN_TOL_S == 10.0
        assert _model().tol_s == 10.0

    def test_is_wider_than_the_shared_analysis_default(self):
        """The stepper is a viewer (every block shows its Δt), so it can afford a
        looser join than the statistics tools, where a loose pairing would
        silently move a correlation coefficient."""
        from canlib.align import DEFAULT_JOIN_TOL_S

        assert DEFAULT_STEP_JOIN_TOL_S > DEFAULT_JOIN_TOL_S

    def test_sits_on_the_ladder_so_both_nudges_work(self):
        assert DEFAULT_STEP_JOIN_TOL_S in TOL_LADDER
        assert TOL_LADDER[0] < DEFAULT_STEP_JOIN_TOL_S < TOL_LADDER[-1]

    def test_joins_a_full_round_robin_cycle(self):
        """The motivating case: two PIDs of one ECU polled ~8.5s apart in the same
        multi-ECU monitor cycle must land in one frame, which 5s split."""
        caps = [
            _entry(pid="220100", time="12:00:00", payload="6201005A6401"),
            _entry(pid="2201A0", time="12:00:08.5", payload="6201005A6402", _capture_idx=1),
        ]
        keys = [("HVAC", "220100"), ("HVAC", "2201A0")]
        assert _model(entries=caps, keys=keys).frame_count() == 1
        assert _model(entries=caps, keys=keys, tol_s=5.0).frame_count() == 2


class TestResolveView:
    def test_auto_stacks_a_small_selection(self):
        assert resolve_view(VIEW_AUTO, 1) == VIEW_STACKED
        assert resolve_view(VIEW_AUTO, AUTO_STACK_MAX_KEYS) == VIEW_STACKED

    def test_auto_interleaves_a_large_selection(self):
        assert resolve_view(VIEW_AUTO, AUTO_STACK_MAX_KEYS + 1) == VIEW_INTERLEAVED

    def test_explicit_view_is_untouched(self):
        assert resolve_view(VIEW_SIGNALS, 99) == VIEW_SIGNALS


class TestStepModelState:
    def test_keys_are_sorted_for_a_stable_block_order(self):
        m = _model(keys=[("HVAC", "2201A2"), ("HVAC", "220100"), ("HVAC", "2201A0")])
        assert m.keys == [("HVAC", "220100"), ("HVAC", "2201A0"), ("HVAC", "2201A2")]

    def test_starts_on_the_most_recent_frame(self):
        m = _model()
        assert m.frame_idx == m.frame_count() - 1

    def test_round_robin_cycles_collapse_to_one_frame_each(self):
        m = _model()
        assert m.frame_count() == 2
        assert m.frame_indices(0) == (0, 1, 2)
        assert m.frame_indices(1) == (3, 4, 5)

    def test_tight_tolerance_splits_the_cycles(self):
        m = _model(tol_s=0.5)
        assert m.frame_count() == 6

    def test_dedupes_payloads_unless_show_all(self):
        entries = [
            _entry(time="12:00:00", payload="6201005A6414", _capture_idx=0),
            _entry(time="12:00:10", payload="6201005A6414", _capture_idx=1),  # duplicate
        ]
        assert len(_model(entries=entries).captures) == 1
        assert len(_model(entries=entries, show_all=True).captures) == 2

    def test_available_keys_reports_counts(self):
        m = _model()
        assert m.available_keys() == [
            (("HVAC", "220100"), 2),
            (("HVAC", "2201A0"), 2),
            (("HVAC", "2201A2"), 2),
        ]

    def test_status_line_summarizes_position_and_settings(self):
        line = _model().status_line()
        assert "frame 2/2" in line
        assert "view stacked" in line
        assert "tol 10s" in line
        assert "3 PIDs" in line
        assert "unique payloads" in line

    def test_untimed_captures_are_reported(self):
        entries = [*_three_pid_entries(), _entry(pid="220100", time="", _capture_idx=9)]
        assert "untimed excluded" in _model(entries=entries).status_line()


class TestStepModelNavigation:
    def test_advance_and_clamp(self):
        m = _model()
        m.first()
        assert m.frame_idx == 0
        assert m.advance(-1) == "At first frame"
        assert m.frame_idx == 0
        m.last()
        assert m.advance(1) == "At last frame"

    def test_advance_beyond_the_end_lands_on_the_last_frame(self):
        m = _model()
        m.first()
        assert m.advance(100) == ""
        assert m.frame_idx == m.frame_count() - 1

    def test_goto_clamps_out_of_range(self):
        m = _model()
        assert m.goto(1) == ""
        assert m.frame_idx == 0
        note = m.goto(999)
        assert "Clamped" in note
        assert m.frame_idx == m.frame_count() - 1

    def test_block_cursor_wraps(self):
        m = _model()
        assert m.block_idx == 0
        m.move_block(1)
        assert m.block_idx == 1
        m.move_block(-2)
        assert m.block_idx == 2  # wrapped backwards past 0

    def test_focused_capture_follows_the_block_cursor(self):
        m = _model()
        m.first()
        assert m.focused_key() == ("HVAC", "220100")
        assert m.focused_capture()["pid"] == "220100"
        m.move_block(1)
        assert m.focused_capture()["pid"] == "2201A0"

    def test_focused_capture_is_none_for_an_empty_slot(self):
        m = _model(tol_s=0.5)  # nothing joins, so each frame has one filled slot
        m.first()
        m.block_idx = 1
        assert m.focused_capture() is None

    def test_interleaved_view_navigates_captures(self):
        m = _model(view=VIEW_INTERLEAVED)
        assert m.frame_count() == 6
        m.first()
        # Only the current capture's own key slot is filled.
        assert m.frame_indices() == (0, None, None)


class TestStepModelMutation:
    def test_changing_tolerance_preserves_the_timeline_position(self):
        m = _model()
        m.first()
        before = m.current_time()
        m.set_tol(0.5)
        assert m.current_time() == before

    def test_removing_a_pid_preserves_the_timeline_position(self):
        m = _model()
        m.first()
        before = m.current_time()
        assert m.remove_key(("HVAC", "2201A2")) is True
        assert m.keys == [("HVAC", "220100"), ("HVAC", "2201A0")]
        assert m.current_time() == before

    def test_cannot_remove_the_last_pid(self):
        m = _model(keys=[("HVAC", "220100")])
        assert m.remove_key(("HVAC", "220100")) is False
        assert m.keys == [("HVAC", "220100")]

    def test_set_keys_can_add_a_pid_not_in_the_original_query(self):
        m = _model(keys=[("HVAC", "220100")])
        assert m.frame_count() == 2
        m.set_keys([("HVAC", "220100"), ("HVAC", "2201A0")])
        assert m.keys == [("HVAC", "220100"), ("HVAC", "2201A0")]
        assert all(len(f.indices) == 2 for f in m.frames)

    def test_nudge_tol_walks_the_ladder(self):
        m = _model()
        assert m.tol_s == DEFAULT_STEP_JOIN_TOL_S  # the ladder starts at the default
        m.nudge_tol(1)
        assert m.tol_s == TOL_LADDER[TOL_LADDER.index(DEFAULT_STEP_JOIN_TOL_S) + 1]
        m.nudge_tol(-1)
        assert m.tol_s == DEFAULT_STEP_JOIN_TOL_S

    def test_nudge_tol_saturates_at_the_ends(self):
        m = _model(tol_s=TOL_LADDER[-1])
        m.nudge_tol(1)
        assert m.tol_s == TOL_LADDER[-1]
        m.set_tol(TOL_LADDER[0])
        m.nudge_tol(-1)
        assert m.tol_s == TOL_LADDER[0]

    def test_cycle_view_rebuilds_when_the_frame_shape_changes(self):
        m = _model(view=VIEW_STACKED)
        assert m.cycle_view() == VIEW_SIGNALS
        assert m.frame_count() == 2  # still stacked frames
        assert m.cycle_view() == VIEW_CHANGED
        assert m.cycle_view() == VIEW_INTERLEAVED
        assert m.frame_count() == 6  # one frame per capture now
        assert m.cycle_view() == VIEW_STACKED
        assert m.frame_count() == 2

    def test_toggles(self):
        m = _model()
        assert m.toggle_rulers() is True
        assert m.rulers is True
        assert m.toggle_show_all() is True
        assert m.show_all is True


class TestStepModelRender:
    def test_stacked_frame_shows_every_key_with_deltas(self):
        m = _model()
        m.first()
        out = _render(m.render())
        assert "frame 1/2" in out
        assert "tol=10s" in out
        assert out.count("HVAC (0x7B3)") == 3
        assert "220100" in out and "2201A0" in out and "2201A2" in out
        assert "Δt=+0.00s" in out and "Δt=+1.00s" in out
        assert "─" in out  # separator between blocks

    def test_missing_slot_is_reported_with_the_tolerance(self):
        m = _model(tol_s=0.5)
        m.first()
        out = _render(m.render())
        assert "no HVAC:2201A0 capture within 0.5s" in out

    def test_signals_view_drops_hex(self):
        m = _model(view=VIEW_SIGNALS)
        out = _render(m.render())
        assert "P_STATIC" in out
        assert "62 01 00" not in out

    def test_interleaved_view_renders_one_capture(self):
        m = _model(view=VIEW_INTERLEAVED)
        out = _render(m.render())
        assert out.count("HVAC (0x7B3)") == 1
        assert "capture 6/6" in out

    def test_block_cursor_can_be_suppressed(self):
        m = _model()
        assert "▶" in _render(m.render())
        m.cursor = False
        assert "▶" not in _render(m.render())

    def test_render_takes_an_explicit_frame(self):
        m = _model()
        assert "frame 1/2" in _render(m.render(0))
        assert "frame 2/2" in _render(m.render(1))

    def test_empty_selection_renders_a_note(self):
        m = _model(entries=[_entry(pid="220100")], keys=[("HVAC", "999999")])
        assert m.is_empty()
        assert "No captures" in _render(m.render())


class TestStepModelJson:
    def test_shape(self):
        m = _model()
        data = m.to_json()
        assert data["view"] == VIEW_STACKED
        assert data["tol_s"] == DEFAULT_STEP_JOIN_TOL_S
        assert data["keys"] == ["HVAC:220100", "HVAC:2201A0", "HVAC:2201A2"]
        assert data["frame_count"] == 2
        assert len(data["frames"]) == 2
        first = data["frames"][0]
        assert first["frame"] == 1
        assert first["time"].startswith("2026-08-02 12:00:00")
        assert [b["key"] for b in first["blocks"]] == [
            "HVAC:220100",
            "HVAC:2201A0",
            "HVAC:2201A2",
        ]
        assert first["blocks"][0]["dt_s"] == 0.0
        assert first["blocks"][1]["dt_s"] == 1.0
        assert first["blocks"][0]["payload"]

    def test_limit_keeps_the_most_recent_frames(self):
        m = _model()
        data = m.to_json(limit=1)
        assert data["frame_count"] == 2
        assert len(data["frames"]) == 1
        assert data["frames"][0]["frame"] == 2

    def test_missing_slot_is_null(self):
        m = _model(tol_s=0.5)
        data = m.to_json(limit=1)
        blocks = data["frames"][0]["blocks"]
        assert sum(1 for b in blocks if b is None) == 2

    def test_is_json_serializable(self):
        json.dumps(_model().to_json(), default=str)


def _write_captures(tmp_path) -> list[dict]:
    """Save two real capture files and return the loaded entries."""
    from canlib.capture_store import load_all_captures
    from canlib.captures import build_query_session, save_session

    save_session(
        build_query_session(
            [
                ("0x7EC", "2101", "6101AA", "12:00:00"),
                ("0x7EC", "2102", "6102BB", "12:00:01"),
            ],
            "step-test",
            ["READY"],
            "",
        ),
        tmp_path,
    )
    return load_all_captures(tmp_path)


class TestCmdStep:
    def test_non_tty_renders_frames_statically(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        entries = _write_captures(tmp_path)
        cmd_step(entries, "BMS", captures_dir=tmp_path)
        out = capsys.readouterr().out
        assert "not a TTY" in out
        assert "2101" in out and "2102" in out
        assert "frame" in out

    def test_non_tty_limit_reports_hidden_frames(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        entries = _write_captures(tmp_path)
        cmd_step(entries, "BMS", captures_dir=tmp_path, tol_s=0.0, limit=1)
        out = capsys.readouterr().out
        assert "earlier frame(s) hidden" in out

    def test_json_output(self, tmp_path, capsys):
        entries = _write_captures(tmp_path)
        capsys.readouterr()  # drop the save banner
        cmd_step(entries, "BMS", captures_dir=tmp_path, as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["keys"] == ["BMS:2101", "BMS:2102"]
        assert data["frames"]

    def test_json_output_when_nothing_matches(self, tmp_path, capsys):
        entries = _write_captures(tmp_path)
        capsys.readouterr()  # drop the save banner
        cmd_step(entries, "BMS:9999", captures_dir=tmp_path, as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["frames"] == [] and data["frame_count"] == 0

    def test_no_match_is_quiet_and_harmless(self, tmp_path, capsys):
        entries = _write_captures(tmp_path)
        cmd_step(entries, "NOSUCHECU", captures_dir=tmp_path)
        assert "No captures matched" in capsys.readouterr().out

    def test_view_flag_is_honored(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        entries = _write_captures(tmp_path)
        cmd_step(entries, "BMS", captures_dir=tmp_path, view=VIEW_INTERLEAVED)
        out = capsys.readouterr().out
        assert "capture 1/2" in out or "capture 2/2" in out


def _plain(renderable) -> str:
    from rich.text import Text

    if isinstance(renderable, Text):
        return renderable.plain
    return _render(renderable)


def _marker_visible(app) -> bool:
    """Whether the ``▶`` block cursor lies inside the scroll viewport."""
    from textual.widgets import Static

    from canlib.tui_scroll import marker_line

    scroll = app.query_one("#scroll", VerticalScroll)
    line = marker_line(app.query_one("#body", Static))
    assert line is not None
    top = int(scroll.scroll_offset.y)
    return top <= line < top + (scroll.size.height or 1)


class TestCapturesStepApp:
    """The Textual shell. Model behavior is covered above; these drive the keys."""

    def _app(self, **kw):
        from canlib.commands.captures.step_tui import CapturesStepApp

        return CapturesStepApp(_model(**kw))

    @pytest.mark.asyncio
    async def test_renders_frame_header_and_status(self):
        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            body = _plain(app.query_one("#body").render())
            status = _plain(app.query_one("#status").render())
            header = _plain(app.query_one("#header").render())
            assert "frame 2/2" in body
            assert "HVAC:220100" in header
            assert "view stacked" in status and "quit" in status

    @pytest.mark.asyncio
    async def test_frame_navigation_keys(self):
        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app.model.frame_idx == 1
            await pilot.press("left")
            assert app.model.frame_idx == 0
            await pilot.press("right")
            assert app.model.frame_idx == 1
            await pilot.press("g")
            assert app.model.frame_idx == 0
            await pilot.press("G")
            assert app.model.frame_idx == 1
            await pilot.press("[")
            assert app.model.frame_idx == 0  # a page back clamps to the first
            await pilot.press("]")
            assert app.model.frame_idx == 1

    @pytest.mark.asyncio
    async def test_scroll_keys_do_not_move_the_frame(self):
        app = self._app()
        async with app.run_test(size=(120, 10)) as pilot:
            await pilot.pause()
            frame = app.model.frame_idx
            await pilot.press("down", "j", "pagedown")
            assert app.model.frame_idx == frame

    @pytest.mark.asyncio
    async def test_frame_moves_keep_the_scroll_position(self):
        """Stepping repaints in place — the viewport must not jump to the top.

        Regression: the stepper used to `scroll_home()` on every frame move, so
        watching one byte deep in a tall stacked frame meant scrolling back down
        after every `→`. The comparison is the whole point of the view.
        """
        app = self._app()
        async with app.run_test(size=(120, 10)) as pilot:
            await pilot.pause()
            scroll = app.query_one("#scroll", VerticalScroll)
            assert scroll.max_scroll_y > 0  # a stacked frame overflows the screen
            await pilot.press("down", "down", "down")
            await pilot.pause()
            parked = int(scroll.scroll_offset.y)
            assert parked > 0
            for key in ("left", "right", "[", "]", "g", "G"):
                await pilot.press(key)
                await pilot.pause()
                assert int(scroll.scroll_offset.y) == parked, f"{key} moved the viewport"

    @pytest.mark.asyncio
    async def test_block_cursor_reveals_an_offscreen_block(self):
        """`tab` is a request to see a block, so it may scroll — only if it must."""
        app = self._app()
        async with app.run_test(size=(120, 10)) as pilot:
            await pilot.pause()
            scroll = app.query_one("#scroll", VerticalScroll)
            assert int(scroll.scroll_offset.y) == 0
            assert _marker_visible(app)  # block 0 is already on screen
            await pilot.press("tab")
            await pilot.pause()
            assert app.model.block_idx == 1
            assert int(scroll.scroll_offset.y) > 0  # had to scroll to show it
            assert _marker_visible(app)

    @pytest.mark.asyncio
    async def test_status_bar_carries_the_frame_timestamp(self):
        """The body's frame header scrolls away, so the bar has to hold the time."""
        app = self._app()
        async with app.run_test(size=(120, 10)) as pilot:
            await pilot.pause()
            status = _plain(app.query_one("#status").render())
            assert "12:01:00.000" in status
            assert "2026-08-02" in status
            await pilot.press("g")
            await pilot.pause()
            assert "12:00:00.000" in _plain(app.query_one("#status").render())

    @pytest.mark.asyncio
    async def test_help_lists_the_framework_scroll_keys(self):
        """Home/End have no Binding to derive a row from, so they're declared."""
        from canlib.commands.captures.step_tui import CapturesStepApp

        rows = dict(CapturesStepApp.HELP_EXTRA_ROWS)
        assert "top / bottom of this frame" in rows.values()
        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("?")
            await pilot.pause()
            help_text = _plain(app.screen.query_one("#help-rows Static").render())
            assert "home/end" in help_text
            assert "next frame" in help_text  # derived rows still there

    @pytest.mark.asyncio
    async def test_tab_moves_the_block_cursor(self):
        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app.model.block_idx == 0
            await pilot.press("tab")
            assert app.model.block_idx == 1
            await pilot.press("shift+tab")
            assert app.model.block_idx == 0

    @pytest.mark.asyncio
    async def test_tab_reaches_the_modal_instead_of_moving_blocks(self):
        """The app claims `tab` with priority; check_action must yield it to a modal."""
        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            before = app.model.block_idx
            focused = app.screen.focused
            await pilot.press("tab")
            await pilot.pause()
            assert app.model.block_idx == before  # block cursor untouched
            assert app.screen.focused is not focused  # focus moved inside the modal

    @pytest.mark.asyncio
    async def test_view_cycle_and_toggles(self):
        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("V")
            assert app.model.view == VIEW_SIGNALS
            await pilot.press("r")
            assert app.model.rulers is True
            await pilot.press("u")
            assert app.model.show_all is True

    @pytest.mark.asyncio
    async def test_tolerance_ladder_keys(self):
        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app.model.tol_s == DEFAULT_STEP_JOIN_TOL_S
            await pilot.press(">")
            assert app.model.tol_s == 30.0
            await pilot.press("<")
            assert app.model.tol_s == DEFAULT_STEP_JOIN_TOL_S

    @pytest.mark.asyncio
    async def test_tolerance_prompt_modal(self):
        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            assert app._modal_active()
            for ch in "0.5":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause()
            assert app.model.tol_s == 0.5
            assert app.model.frame_count() == 6  # re-joined tighter

    @pytest.mark.asyncio
    async def test_tolerance_prompt_rejects_non_numeric(self):
        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("ctrl+a")  # select existing value
            for ch in "abc":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause()
            assert app.model.tol_s == DEFAULT_STEP_JOIN_TOL_S
            assert "Not a number" in app._flash_msg

    @pytest.mark.asyncio
    async def test_goto_modal(self):
        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press(":")
            await pilot.pause()
            await pilot.press("1")
            await pilot.press("enter")
            await pilot.pause()
            assert app.model.frame_idx == 0

    @pytest.mark.asyncio
    async def test_drop_pid_key(self):
        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            assert app.model.keys == [("HVAC", "2201A0"), ("HVAC", "2201A2")]
            assert "Dropped HVAC:220100" in app._flash_msg

    @pytest.mark.asyncio
    async def test_drop_pid_refuses_the_last_one(self):
        app = self._app(keys=[("HVAC", "220100")])
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            assert app.model.keys == [("HVAC", "220100")]
            assert "Cannot drop the last PID" in app._flash_msg

    @pytest.mark.asyncio
    async def test_pid_modal_removes_and_adds(self):
        from textual.widgets import SelectionList

        app = self._app(keys=[("HVAC", "220100")])
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            lst = app.screen.query_one("#pid-list", SelectionList)
            assert lst.selected == [("HVAC", "220100")]  # pre-checked
            lst.select_all()
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert app.model.keys == [
                ("HVAC", "220100"),
                ("HVAC", "2201A0"),
                ("HVAC", "2201A2"),
            ]

    @pytest.mark.asyncio
    async def test_pid_modal_cancel_changes_nothing(self):
        app = self._app()
        before = list(app.model.keys)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.model.keys == before

    @pytest.mark.asyncio
    async def test_pid_modal_filter_preserves_checks_out_of_view(self):
        from textual.widgets import Input, SelectionList

        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            modal = app.screen
            modal.query_one("#pid-filter", Input).value = "2201A0"
            await pilot.pause()
            lst = modal.query_one("#pid-list", SelectionList)
            assert lst.option_count == 1
            await pilot.press("ctrl+s")
            await pilot.pause()
            # The two PIDs filtered out of view stayed selected.
            assert len(app.model.keys) == 3

    @pytest.mark.asyncio
    async def test_help_modal_lists_bindings(self):
        from canlib.tui_help import bindings_help_rows

        app = self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            rows = bindings_help_rows(app)
            descs = {d for _k, d in rows}
            assert "add/remove PIDs" in descs
            assert "join tolerance" in descs
            assert "view mode" in descs
            await pilot.press("?")
            await pilot.pause()
            assert app._modal_active()


class TestCapturesStepAppEdits:
    """Note/delete act on the focused block and write through canlib.captures."""

    def _app(self, tmp_path):
        from canlib.commands.captures.step import build_model
        from canlib.commands.captures.step_tui import CapturesStepApp

        entries = _write_captures(tmp_path)
        model = build_model(entries, "BMS", captures_dir=tmp_path, warn=False)
        assert model is not None
        return CapturesStepApp(model)

    @pytest.mark.asyncio
    async def test_edit_note_writes_and_reloads(self, tmp_path):
        app = self._app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            target = app.model.focused_capture()
            await pilot.press("e")
            await pilot.pause()
            for ch in "hello":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause()
            assert "Note saved" in app._flash_msg
        doc = json.loads((tmp_path / f"{target['date']}.json").read_text())
        notes = [c.get("notes") for c in doc["sessions"][0]["captures"]]
        assert "hello" in notes

    @pytest.mark.asyncio
    async def test_delete_requires_confirmation(self, tmp_path):
        app = self._app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            before = len(app.model.captures)
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("escape")  # decline
            await pilot.pause()
            assert "Delete cancelled" in app._flash_msg
            assert len(app.model.captures) == before

    @pytest.mark.asyncio
    async def test_delete_removes_the_focused_capture(self, tmp_path):
        app = self._app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            before = len(app.model.captures)
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert len(app.model.captures) == before - 1


def _jump_entries() -> list[dict]:
    """Two timed sessions plus one legacy untimed one, with assorted notes."""
    out: list[dict] = []
    # Session 0 (older): HVAC 220100 + 2201A0, one note on each PID.
    for n, (pid, t, note) in enumerate(
        [
            ("220100", "12:00:00", ""),
            ("2201A0", "12:00:01", "Heating started"),
            ("220100", "12:00:10", "compressor engaged"),
            ("2201A0", "12:00:11", ""),
        ]
    ):
        out.append(
            _entry(
                file="2026-08-01.json",
                date="2026-08-01",
                session_label="older session",
                vehicle_states=["READY"],
                pid=pid,
                time=t,
                payload=f"6201005A640{n}",
                notes=note,
                _session_idx=0,
                _capture_idx=n,
            )
        )
    # Session 1 (newer, different file): a note on a PID and a non-payload note.
    out.append(
        _entry(
            file="2026-08-02.json",
            date="2026-08-02",
            session_label="newer session",
            pid="220100",
            time="13:00:00",
            payload="6201005A64AA",
            notes="newer note",
            _session_idx=0,
            _capture_idx=0,
        )
    )
    out.append(
        _entry(
            file="2026-08-02.json",
            date="2026-08-02",
            session_label="newer session",
            pid="21F2",
            time="13:00:01",
            payload=None,
            response="NO DATA",
            notes="NRC 0x11 serviceNotSupported",
            _session_idx=0,
            _capture_idx=1,
        )
    )
    # A session with no notes at all, so the notes-only filter has work to do.
    out.append(
        _entry(
            file="2026-07-15.json",
            date="2026-07-15",
            session_label="unannotated session",
            pid="220100",
            time="09:00:00",
            payload="6201005A64BB",
            notes="",
            _session_idx=0,
            _capture_idx=0,
        )
    )
    # Legacy untimed session — no timestamp, so it cannot be placed on a timeline.
    out.append(
        _entry(
            file="2026-07-01.json",
            date="2026-07-01",
            session_label="legacy untimed",
            pid="220100",
            time="",
            payload="6201005A64FF",
            notes="legacy note",
            _session_idx=0,
            _capture_idx=0,
        )
    )
    return out


def _jump_model(**kw) -> StepModel:
    entries = _jump_entries()
    keys = kw.pop("keys", [("HVAC", "220100")])
    defs = dict.fromkeys({(e["ecu"], str(e["pid"])) for e in entries} | set(keys), (PARAMS, 0x7B3))
    kw.setdefault("captures_dir", None)
    return StepModel.from_entries(entries, keys, defs, **kw)


class TestJumpTargets:
    def test_sessions_are_listed_newest_first(self):
        rows = [t for t in _jump_model().jump_targets().rows if not t.is_note]
        # 2026-07-01 is the untimed legacy session: no frame, and its only note
        # cannot be placed on a timeline, so it offers nowhere to go.
        assert [t.session[0] for t in rows] == [
            "2026-08-02.json",
            "2026-08-01.json",
            "2026-07-15.json",
        ]

    def test_notes_nest_under_their_session(self):
        rows = _jump_model().jump_targets().rows
        # Each note row carries the session of the header immediately above it.
        current = None
        for t in rows:
            if not t.is_note:
                current = t.session
            else:
                assert t.session == current

    def test_session_row_shows_span_states_and_counts(self):
        row = next(
            t
            for t in _jump_model().jump_targets().rows
            if not t.is_note and t.session[0] == "2026-08-01.json"
        )
        assert "2026-08-01" in row.label
        assert "12:00:00-12:00:11" in row.label
        assert "[READY]" in row.label
        assert "older session" in row.label
        assert "4 caps" in row.detail and "2 notes" in row.detail

    def test_note_row_shows_time_pid_and_text(self):
        row = next(t for t in _jump_model().jump_targets().rows if t.label == "Heating started")
        assert "12:00:01" in row.detail
        assert "HVAC:2201A0" in row.detail
        assert row.ref == ("2026-08-01.json", 0, 1)
        assert row.key == ("HVAC", "2201A0")

    def test_untimed_session_shows_no_span(self):
        # Only listed where it is reachable — the interleaved view needs no times.
        row = next(
            t
            for t in _jump_model(view=VIEW_INTERLEAVED).jump_targets().rows
            if not t.is_note and t.session[0] == "2026-07-01.json"
        )
        assert "—" in row.label

    def test_multiline_note_is_collapsed_to_one_line(self):
        entries = _jump_entries()
        entries[1]["notes"] = "first line\n  second line"
        keys = [("HVAC", "220100")]
        defs = dict.fromkeys(
            {(e["ecu"], str(e["pid"])) for e in entries} | set(keys), (PARAMS, 0x7B3)
        )
        m = StepModel.from_entries(entries, keys, defs)
        row = next(t for t in m.jump_targets().rows if t.is_note and "first line" in t.label)
        assert row.label == "first line second line"


class TestJumpBlocking:
    def test_non_payload_note_is_blocked_with_a_reason(self):
        row = next(t for t in _jump_model().jump_targets().rows if t.label.startswith("NRC 0x11"))
        assert row.blocked == BLOCK_NON_PAYLOAD

    def test_untimed_note_is_omitted_from_the_stacked_view_and_counted(self):
        """The stacked views have no frame for an untimed capture, and this
        profile's legacy notes are overwhelmingly untimed — listing them all as
        dead rows would bury the reachable ones."""
        jl = _jump_model().jump_targets()
        assert not any(t.label == "legacy note" for t in jl.rows)
        assert jl.hidden_notes == 1

    def test_untimed_note_is_listed_and_reachable_in_the_interleaved_view(self):
        jl = _jump_model(view=VIEW_INTERLEAVED).jump_targets()
        row = next(t for t in jl.rows if t.label == "legacy note")
        assert row.blocked == ""
        assert jl.hidden_notes == 0

    def test_a_timed_non_payload_note_is_still_listed_with_its_reason(self):
        """It is invisible in every view, so flagging it is the only way to
        surface it at all — unlike an untimed note, which is one view away."""
        row = next(t for t in _jump_model().jump_targets().rows if t.label.startswith("NRC 0x11"))
        assert row.blocked == BLOCK_NON_PAYLOAD

    def test_session_without_a_frame_is_kept_as_a_heading_for_its_notes(self):
        m = _jump_model(keys=[("HVAC", "2201A0")])
        rows = m.jump_targets().rows
        row = next(t for t in rows if not t.is_note and t.session[0] == "2026-08-02.json")
        # 2026-08-02 has only 220100 + a non-payload capture, so there is no
        # frame for it — but it carries notes, so it stays as their heading.
        assert row.blocked == BLOCK_NO_FRAME
        assert any(t.is_note and t.session == row.session for t in rows)

    def test_session_offering_nothing_is_omitted_and_counted(self):
        """A session with no frame *and* no notes is noise, not a target."""
        m = _jump_model(keys=[("HVAC", "2201A0")])
        jl = m.jump_targets()
        listed = {t.session[0] for t in jl.rows}
        # 2026-07-15 holds only an unannotated 220100 capture.
        assert "2026-07-15.json" not in listed
        assert jl.hidden_sessions >= 1

    def test_nothing_is_hidden_when_every_session_is_relevant(self):
        m = _jump_model(keys=[("HVAC", "220100"), ("HVAC", "2201A0")], view=VIEW_INTERLEAVED)
        jl = m.jump_targets()
        assert jl.hidden_sessions == 0 and jl.hidden_notes == 0

    def test_note_on_an_unselected_pid_is_not_blocked(self):
        """It is reachable — seek_capture adds the PID rather than refusing."""
        m = _jump_model(keys=[("HVAC", "220100")])
        row = next(t for t in m.jump_targets().rows if t.label == "Heating started")
        assert row.key == ("HVAC", "2201A0") and row.blocked == ""


class TestSeek:
    def test_seek_session_lands_on_its_first_frame(self):
        m = _jump_model()
        m.last()
        msg = m.seek_session(("2026-08-01.json", 0))
        assert "Jumped to" in msg
        assert m.frame_time().date().isoformat() == "2026-08-01"

    def test_seek_session_reports_when_unreachable(self):
        m = _jump_model(keys=[("HVAC", "2201A0")])
        assert "No frame" in m.seek_session(("2026-08-02.json", 0))

    def test_seek_capture_lands_on_the_note(self):
        m = _jump_model()
        ref = ("2026-08-01.json", 0, 2)  # "compressor engaged" on 220100
        assert "Jumped to note" in m.seek_capture(ref, ("HVAC", "220100"))
        assert m.captures[m._at[ref]]["notes"] == "compressor engaged"

    def test_seek_capture_adds_a_missing_pid_and_says_so(self):
        m = _jump_model(keys=[("HVAC", "220100")])
        msg = m.seek_capture(("2026-08-01.json", 0, 1), ("HVAC", "2201A0"))
        assert "added HVAC:2201A0" in msg
        assert ("HVAC", "2201A0") in m.keys

    def test_seek_capture_lifts_dedup_when_it_hid_the_target(self):
        entries = _jump_entries()
        # Make the noted capture a duplicate payload of an earlier one, so the
        # default unique-payload filter drops it.
        entries[2]["payload"] = entries[0]["payload"]
        keys = [("HVAC", "220100")]
        defs = dict.fromkeys(
            {(e["ecu"], str(e["pid"])) for e in entries} | set(keys), (PARAMS, 0x7B3)
        )
        m = StepModel.from_entries(entries, keys, defs)
        ref = ("2026-08-01.json", 0, 2)
        assert ref not in m._at  # deduped away
        msg = m.seek_capture(ref, ("HVAC", "220100"))
        assert "all payloads on" in msg
        assert m.show_all is True
        assert ref in m._at

    def test_seek_capture_focuses_the_block_showing_it(self):
        m = _jump_model(keys=[("HVAC", "220100"), ("HVAC", "2201A0")])
        m.seek_capture(("2026-08-01.json", 0, 1), ("HVAC", "2201A0"))
        assert m.focused_key() == ("HVAC", "2201A0")
        assert m.focused_capture()["notes"] == "Heating started"

    def test_locators_survive_a_rebuild(self):
        """Capture indices are invalidated by a rebuild; locators must not be."""
        m = _jump_model(keys=[("HVAC", "220100"), ("HVAC", "2201A0")])
        ref = ("2026-08-01.json", 0, 1)
        before = m._at[ref]
        m.set_tol(0.25)
        m.toggle_show_all()
        assert ref in m._at
        assert isinstance(before, int)
        assert "Jumped to note" in m.seek_capture(ref, ("HVAC", "2201A0"))


class TestEmptyReason:
    def test_untimed_only_selection_explains_itself(self):
        """Regression: an all-untimed selection reported a bare 'No captures'
        and a nonsensical 'frame 1/0'."""
        entries = [e for e in _jump_entries() if e["file"] == "2026-07-01.json"]
        keys = [("HVAC", "220100")]
        m = StepModel.from_entries(entries, keys, dict.fromkeys(keys, (PARAMS, 0x7B3)))
        assert m.is_empty()
        assert m.captures and m.n_no_time == 1
        assert "untimed" in m.empty_reason()
        assert "no frames" in m.status_line()
        assert "frame 1/0" not in m.status_line()
        assert "untimed" in _render(m.render())

    def test_genuinely_empty_selection_says_so(self):
        m = _jump_model(keys=[("HVAC", "999999")])
        assert m.empty_reason() == "No captures for the selected PIDs."


class TestJumpModal:
    def _open(self, model=None):
        from canlib.commands.captures.step_tui import CapturesStepApp

        return CapturesStepApp(model or _jump_model())

    @pytest.mark.asyncio
    async def test_s_opens_the_modal_listing_sessions_and_notes(self):
        from canlib.commands.captures.step_tui import JumpModal

        app = self._open()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, JumpModal)
            rows = [app.screen._row(t).plain for t in app.screen._shown]
            assert any("older session" in r for r in rows)
            assert any("Heating started" in r for r in rows)
            # A non-payload note is listed with its reason; an untimed one is not
            # listed at all in this (stacked) view.
            assert any(BLOCK_NON_PAYLOAD in r for r in rows)
            assert not any("legacy note" in r for r in rows)

    @pytest.mark.asyncio
    async def test_free_text_is_not_parsed_as_markup(self):
        """Regression: a session's `[READY]` state vanished as a Rich style tag."""
        app = self._open()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            rows = [app.screen._row(t).plain for t in app.screen._shown]
            assert any("[READY]" in r for r in rows)

    @pytest.mark.asyncio
    async def test_rows_never_wrap(self):
        app = self._open()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            from canlib.commands.captures.step_tui import _JUMP_ROW_WIDTH

            for t in app.screen._shown:
                assert len(app.screen._row(t).plain) <= _JUMP_ROW_WIDTH

    @pytest.mark.asyncio
    async def test_unreachable_rows_are_disabled(self):
        from textual.widgets import OptionList

        app = self._open()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            lst = app.screen.query_one("#jump-list", OptionList)
            blocked = [n for n, t in enumerate(app.screen._shown) if t.blocked]
            assert blocked, "fixture should contain unreachable rows"
            for n in blocked:
                assert lst.get_option_at_index(n).disabled
            # The initial highlight skips them.
            assert lst.highlighted not in blocked

    @pytest.mark.asyncio
    async def test_session_rows_carry_no_inline_reason(self):
        """A blocked session row is just a heading; "(not in this selection)"
        inline there reads as noise rather than information."""
        app = self._open(_jump_model(keys=[("HVAC", "2201A0")]))
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            for t in app.screen._shown:
                row = app.screen._row(t).plain
                if not t.is_note:
                    assert BLOCK_NO_FRAME not in row
            # A note still explains itself.
            assert any(
                BLOCK_NON_PAYLOAD in app.screen._row(t).plain
                for t in app.screen._shown
                if t.is_note
            )

    @pytest.mark.asyncio
    async def test_footer_reports_hidden_sessions(self):
        app = self._open(_jump_model(keys=[("HVAC", "2201A0")]))
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            footer = app.screen.query_one("#jump-footer").render()
            text = footer.plain if hasattr(footer, "plain") else str(footer)
            assert "sessions hidden" in text

    @pytest.mark.asyncio
    async def test_footer_reports_dropped_untimed_notes(self):
        """Dropping 300+ legacy notes silently would be worse than the noise —
        the footer says how many and where to read them."""
        app = self._open()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            footer = app.screen.query_one("#jump-footer").render()
            text = footer.plain if hasattr(footer, "plain") else str(footer)
            assert "untimed notes hidden" in text
            assert "captures --sessions" in text

    @pytest.mark.asyncio
    async def test_notes_only_toggle(self):
        app = self._open()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            everything = len(app.screen._shown)
            await pilot.press("n")
            await pilot.pause()
            assert len(app.screen._shown) < everything
            # Only noted sessions and their notes survive.
            assert all(
                t.is_note or any(o.is_note and o.session == t.session for o in app.screen._shown)
                for t in app.screen._shown
            )

    @pytest.mark.asyncio
    async def test_filter_matches_note_text_and_keeps_its_session_header(self):
        from textual.widgets import Input

        app = self._open()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            app.screen.query_one("#jump-filter", Input).value = "heating"
            await pilot.pause()
            rows = [app.screen._row(t).plain for t in app.screen._shown]
            assert any("Heating started" in r for r in rows)
            assert any("older session" in r for r in rows)  # header retained
            assert not any("newer note" in r for r in rows)

    @pytest.mark.asyncio
    async def test_selecting_a_note_jumps_and_flashes(self):
        app = self._open(_jump_model(keys=[("HVAC", "220100")]))
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            target = next(
                n for n, t in enumerate(app.screen._shown) if t.label == "Heating started"
            )
            app.screen.query_one("#jump-list").highlighted = target
            await pilot.press("enter")
            await pilot.pause()
            assert "added HVAC:2201A0" in app._flash_msg
            assert ("HVAC", "2201A0") in app.model.keys
            assert app.model.focused_capture()["notes"] == "Heating started"

    @pytest.mark.asyncio
    async def test_selecting_a_session_jumps_to_its_start(self):
        app = self._open()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            target = next(
                n
                for n, t in enumerate(app.screen._shown)
                if not t.is_note and t.session[0] == "2026-08-01.json"
            )
            app.screen.query_one("#jump-list").highlighted = target
            await pilot.press("enter")
            await pilot.pause()
            assert "Jumped to 2026-08-01.json#0" in app._flash_msg
            assert app.model.frame_time().date().isoformat() == "2026-08-01"

    @pytest.mark.asyncio
    async def test_cancel_changes_nothing(self):
        app = self._open()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            before = (app.model.frame_idx, list(app.model.keys))
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert (app.model.frame_idx, list(app.model.keys)) == before

    @pytest.mark.asyncio
    async def test_jump_is_advertised_in_the_help(self):
        from canlib.tui_help import bindings_help_rows

        app = self._open()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            assert ("s", "sessions & notes") in bindings_help_rows(app)
