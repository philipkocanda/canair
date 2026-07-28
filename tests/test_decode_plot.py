"""Tests for decode.py --plot helpers: byte interpretation, transforms, rendering."""

import math

from canlib.commands import _decode_plot as dp
from canlib.inspect_bytes import InspectType

U16 = InspectType("u16", 2, "int", False)

FRAME = bytes([0x04, 0x61, 0x01, 0xAB, 0xCD, 0x00])  # PCI, SID, echo, data...

_MAP_DEFS = {
    "MCU_MOTOR_RPM": {"expression": "[S10:S11]", "verified": True},
    "OTHER": {"expression": "B20", "verified": False},
}

_VIEW_CAPS = [
    {
        "date": "2026-07-19",
        "time": "22:12:07",
        "vehicle_states": ["driving"],
        "label": "launch",
        "notes": "hard accel\nregen",
        "file": "a.yaml",
    },
    {
        "date": "2026-07-20",
        "time": "14:03:11",
        "vehicle_states": ["ready"],
        "label": "",
        "notes": "",
        "file": "b.yaml",
    },
]


class TestInterpretBytes:
    def test_endianness(self):
        # Spot-check that the plot module still exposes the shared primitive
        # (imported from canlib.inspect_bytes); exhaustive coverage lives in
        # tests/test_inspect_bytes.py.
        assert dp.interpret_bytes(FRAME, 3, U16) == 0xABCD


class TestRendering:
    def test_pci_positions(self):
        # 6101ABCD -> frame 04 61 01 AB CD; only B0 is PCI.
        assert dp._pci_positions("6101ABCD") == {0}

    def test_render_plot_shape(self):
        lines = dp.render_plot([1, 2, 3, 2, 1], width=20, height=6)
        assert len(lines) == 8  # height + axis + caption
        assert any("\u2802" <= ch <= "\u28ff" for ch in "".join(lines))  # has braille

    def test_render_plot_empty(self):
        assert dp.render_plot([]) == ["  (no data to plot)"]

    def test_overlay_normalizes(self):
        # With a ref, both series are normalized; caption notes it.
        lines = dp.render_plot([10, 20, 30], ref=[1, 2, 3], width=20, height=6)
        assert any("normalized" in ln for ln in lines)

    def test_caption_override(self):
        lines = dp.render_plot([1, 2, 3], width=20, height=6, caption="captures 0-2 of 3")
        assert any("captures 0-2 of 3" in ln for ln in lines)


class TestWindow:
    def test_full_range(self):
        assert dp._window(list(range(100)), 0.0, 1.0)[1:] == (0, 100)

    def test_zoomed_subrange(self):
        view, i0, i1 = dp._window(list(range(100)), 0.25, 0.5)
        assert (i0, i1) == (25, 50) and view == list(range(25, 50))

    def test_empty(self):
        assert dp._window([], 0.0, 1.0) == ([], 0, 0)

    def test_always_at_least_one_point(self):
        # A degenerate window still yields a non-empty slice.
        view, i0, i1 = dp._window([1, 2, 3, 4], 0.99, 0.99)
        assert len(view) >= 1 and i1 > i0


class TestMappingForOffset:
    def test_exact_match(self):
        exact, overlap = dp._mapping_for_offset(_MAP_DEFS, 10, 2, "[S10:S11]")
        assert exact == [("MCU_MOTOR_RPM", "[S10:S11]", True)]
        assert overlap == []

    def test_overlap_not_exact(self):
        # B11 is read by [S10:S11] but the expression differs -> overlap, not exact.
        exact, overlap = dp._mapping_for_offset(_MAP_DEFS, 11, 1, "B11")
        assert exact == []
        assert overlap == [("MCU_MOTOR_RPM", "[S10:S11]", True)]

    def test_unmapped(self):
        assert dp._mapping_for_offset(_MAP_DEFS, 50, 1, "B50") == ([], [])

    def test_none_current_expr_still_finds_overlap(self):
        # A float interpretation (no WiCAN expr) can still report byte overlap.
        exact, overlap = dp._mapping_for_offset(_MAP_DEFS, 20, 4, None)
        assert exact == [] and overlap and overlap[0][0] == "OTHER"

    def test_empty_defs(self):
        assert dp._mapping_for_offset({}, 10, 2, "[S10:S11]") == ([], [])


class TestNonFinite:
    """Float byte-interpretations can yield NaN/Inf — must never crash the plot."""

    def test_fmt_num_handles_nonfinite(self):
        assert dp._fmt_num(float("nan")) == "nan"
        assert dp._fmt_num(float("inf")) == "inf"
        assert dp._fmt_num(float("-inf")) == "-inf"

    def test_float_interpretation_can_be_nan(self):
        nan_bytes = bytes([0x7F, 0xC0, 0x00, 0x00])  # IEEE-754 quiet NaN, big-endian
        v = dp.interpret_bytes(nan_bytes, 0, InspectType("f32", 4, "float", True))
        assert v is not None and math.isnan(v)
        inf_bytes = bytes([0x7F, 0x80, 0x00, 0x00])  # +Inf
        assert dp.interpret_bytes(inf_bytes, 0, InspectType("f32", 4, "float", True)) == math.inf

    def test_stats_str_survives_stray_nan(self):
        # Backstop: a NaN reaching the stats line renders instead of raising.
        assert "nan" in dp._series_stats_str([1.0, float("nan"), 3.0])


class TestCaptureView:
    def test_cap_ts_combines_date_and_time(self):
        assert dp._cap_ts(_VIEW_CAPS[0]) == "2026-07-19 22:12:07"
        assert dp._cap_ts({"date": "2026-07-19", "time": ""}) == "2026-07-19"
        assert dp._cap_ts({"date": "", "time": ""}) == ""

    def test_view_time_range(self):
        assert dp._view_time_range(_VIEW_CAPS) == ("2026-07-19 22:12:07", "2026-07-20 14:03:11")

    def test_view_time_range_no_timestamps(self):
        assert dp._view_time_range([{"date": "", "time": ""}]) == ("", "")

    def test_info_lines_contents(self):
        lines = dp._info_lines(
            "MCU",
            "2102",
            _VIEW_CAPS,
            i0=40,
            total=332,
            ts_range="2026-07-19 → 2026-07-20",
            max_rows=20,
        )
        body = "\n".join(lines)
        assert "captures in view" in body
        assert "launch" in body and "driving" in body
        assert "hard accel regen" in body  # notes flattened
        assert "a.yaml" in body and "b.yaml" in body
        assert "40" in body and "41" in body  # global indices (i0 + n)

    def test_info_lines_truncates(self):
        many = [dict(_VIEW_CAPS[0], label=f"c{i}") for i in range(50)]
        lines = dp._info_lines("MCU", "2102", many, 0, 50, "r", max_rows=10)
        assert any("and 40 more" in ln for ln in lines)


class TestOverlayCycle:
    def test_cycles_through_all_and_wraps(self):
        cyc = [None, "A", "B"]
        assert dp._cycle_overlay(None, cyc) == "A"  # off -> first param
        assert dp._cycle_overlay("A", cyc) == "B"
        assert dp._cycle_overlay("B", cyc) is None  # wraps back to off

    def test_works_without_corr_when_params_exist(self):
        # The reported bug: `o` did nothing without --corr. Any numeric param is
        # a valid overlay reference, so cycling engages an overlay.
        cyc = [None, "MCU_MOTOR_RPM"]
        assert dp._cycle_overlay(None, cyc) == "MCU_MOTOR_RPM"

    def test_noop_when_no_candidates(self):
        assert dp._cycle_overlay(None, [None]) is None

    def test_unknown_ref_restarts(self):
        # A stale ref not in the cycle restarts from the first entry.
        assert dp._cycle_overlay("GONE", [None, "A"]) == "A"


# ---------------------------------------------------------------------------
# PlotModel (state + rendering extracted for the Textual port) and PlotApp
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

from canlib.commands._decode_plot import INSPECT_TYPES, PlotModel  # noqa: E402


def _plot_results(values, pid="2101", ecu="BMS"):
    out = []
    for i, v in enumerate(values):
        out.append(
            {
                "capture": {
                    "payload": "6101" + f"{int(v) & 0xFF:02X}" + "00",
                    "date": "2026-07-22",
                    "time": f"12:00:{i:02d}",
                    "ecu": ecu,
                    "pid": pid,
                },
                "decoded": {"P": {"value": float(v)}},
            }
        )
    return out


def _plot_model(values=(1, 2, 3, 4, 5)):
    results = _plot_results(values)
    params = {"P": {"expression": "B4", "verified": True}}
    return PlotModel(results, ["P"], params, set(), None, "BMS", "2101", defined_params=params)


class TestPlotModel:
    def test_initial_state_bytes_mode(self):
        m = _plot_model()
        assert m.mode == "bytes"
        assert not m.empty
        assert any("BMS 2101" in ln for ln in m.render_lines())

    def test_toggle_mode_and_param_render(self):
        m = _plot_model()
        m.toggle_mode()
        assert m.mode == "param"
        assert m.current_param_name() == "P"
        assert any("P" in ln for ln in m.render_lines())

    def test_offset_navigation_clamps(self):
        m = _plot_model()
        for _ in range(100):
            m.move_right()
        assert m.offset == m.max_off
        for _ in range(100):
            m.move_left()
        assert m.offset == 0

    def test_type_cycle_wraps(self):
        m = _plot_model()
        m.ti = len(INSPECT_TYPES) - 1
        m.type_next()
        assert m.ti == 0
        m.type_prev()
        assert m.ti == len(INSPECT_TYPES) - 1

    def test_transform_cycle(self):
        m = _plot_model()
        assert m.tmode == "raw"
        m.cycle_transform()
        assert m.tmode != "raw"

    def test_zoom_and_reset(self):
        m = _plot_model()
        m.zoom_in()
        assert (m.xlo, m.xhi) != (0.0, 1.0)
        m.reset_x()
        assert (m.xlo, m.xhi) == (0.0, 1.0)

    def test_current_expr_bytes_mode(self):
        m = _plot_model()
        m.offset = 4
        m.ti = 0  # u8
        assert m.current_expr() == "B4"

    def test_empty_model(self):
        assert PlotModel([], [], {}, set(), None, "BMS", "2101").empty


class TestPlotApp:
    @pytest.mark.asyncio
    async def test_renders_and_navigates(self):
        from canlib.commands._decode_plot_tui import PlotApp

        app = PlotApp(_plot_model())
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            body = app.query_one("#body").render()
            plain = body.plain if hasattr(body, "plain") else str(body)
            assert "BMS 2101" in plain
            await pilot.press("m")  # to param mode
            await pilot.pause(0.05)
            assert app.model.mode == "param"
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_help_modal(self):
        from canlib.commands._decode_plot_tui import PlotApp
        from canlib.tui_help import HelpModal

        app = PlotApp(_plot_model())
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen, HelpModal)
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_pid_switch_reloads_model(self):
        from canlib.commands._decode_plot_tui import PlotApp

        other = _plot_model((9, 8, 7))
        other.ecu_key, other.pid_key = "VCU", "2102"

        def reload(ecu, pid):
            return other if (ecu, pid) == ("VCU", "2102") else None

        app = PlotApp(_plot_model(), reload_pid=reload, pid_options=[("VCU", "2102")])
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("p")
            await pilot.pause(0.1)
            await pilot.press("enter")  # select the only option
            await pilot.pause(0.1)
            assert app.model is other
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_annotate_param_calls_upsert(self, monkeypatch):
        from canlib.commands._decode_plot_tui import PlotApp

        calls = []

        def fake_upsert(ecu, pid, name, expr, **kw):
            calls.append((ecu, pid, name, expr, kw))
            return None

        monkeypatch.setattr("canlib.pids_edit.upsert_parameter", fake_upsert)

        app = PlotApp(_plot_model())
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("m")  # param mode -> annotate current param P
            await pilot.pause(0.05)
            await pilot.press("a")
            await pilot.pause(0.1)
            from textual.widgets import Input

            app.screen.query_one("#prompt-input", Input).value = "a note"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert calls and calls[0][2] == "P"
            assert calls[0][4].get("notes") == "a note"
            await pilot.press("q")
