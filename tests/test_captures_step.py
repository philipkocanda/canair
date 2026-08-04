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

from canlib.commands._captures_step import cmd_step
from canlib.commands._captures_step_model import (
    AUTO_STACK_MAX_KEYS,
    TOL_LADDER,
    VIEW_AUTO,
    VIEW_CHANGED,
    VIEW_INTERLEAVED,
    VIEW_SIGNALS,
    VIEW_STACKED,
    StepModel,
    resolve_view,
)
from canlib.commands._captures_step_render import capture_block_text

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
        assert "no param changes" in out

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
        assert "tol 5s" in line
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
        m = _model(tol_s=5.0)
        m.nudge_tol(1)
        assert m.tol_s == TOL_LADDER[TOL_LADDER.index(5.0) + 1]
        m.nudge_tol(-1)
        assert m.tol_s == 5.0

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
        assert "tol=5s" in out
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
        assert data["tol_s"] == 5.0
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
    from canlib.captures import build_query_session, save_session
    from canlib.commands._captures_query import load_all_captures

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


class TestCapturesStepApp:
    """The Textual shell. Model behavior is covered above; these drive the keys."""

    def _app(self, **kw):
        from canlib.commands._captures_step_tui import CapturesStepApp

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
            await pilot.press(">")
            assert app.model.tol_s == 10.0
            await pilot.press("<")
            assert app.model.tol_s == 5.0

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
            assert app.model.tol_s == 5.0
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
        from canlib.commands._captures_step import build_model
        from canlib.commands._captures_step_tui import CapturesStepApp

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
