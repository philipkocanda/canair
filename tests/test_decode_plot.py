"""Tests for decode.py --plot helpers: byte interpretation, transforms, rendering."""

import math

from canlib.commands import decode as dp

U8 = ("u8", 1, "int", False)
I8 = ("i8", 1, "int", True)
U16 = ("u16", 2, "int", False)
I16 = ("i16", 2, "int", True)
U24 = ("u24", 3, "int", False)
U32 = ("u32", 4, "int", False)
F32 = ("f32", 4, "float", True)

FRAME = bytes([0x04, 0x61, 0x01, 0xAB, 0xCD, 0x00])  # PCI, SID, echo, data...

_MAP_DEFS = {
    "MCU_MOTOR_RPM": {"expression": "[S10:S11]", "verified": True},
    "OTHER": {"expression": "B20", "verified": False},
}

_VIEW_CAPS = [
    {"date": "2026-07-19", "time": "22:12:07", "vehicle_states": ["driving"],
     "label": "launch", "notes": "hard accel\nregen", "file": "a.yaml"},
    {"date": "2026-07-20", "time": "14:03:11", "vehicle_states": ["ready"],
     "label": "", "notes": "", "file": "b.yaml"},
]


class TestInterpretBytes:
    def test_unsigned_and_signed_byte(self):
        assert dp.interpret_bytes(FRAME, 3, U8) == 171.0
        assert dp.interpret_bytes(FRAME, 3, I8) == -85.0

    def test_endianness(self):
        assert dp.interpret_bytes(FRAME, 3, U16) == 0xABCD          # big-endian
        assert dp.interpret_bytes(FRAME, 3, U16, little=True) == 0xCDAB
        assert dp.interpret_bytes(FRAME, 3, I16) == 0xABCD - 0x10000  # signed BE

    def test_u24_u32(self):
        assert dp.interpret_bytes(FRAME, 3, U24) == 0xABCD00
        assert dp.interpret_bytes(FRAME, 2, U32) == 0x01ABCD00

    def test_out_of_range_returns_none(self):
        assert dp.interpret_bytes(FRAME, 5, U16) is None   # only 1 byte left
        assert dp.interpret_bytes(FRAME, -1, U8) is None

    def test_float_roundtrips(self):
        import struct
        raw = struct.pack(">f", 3.5)
        assert dp.interpret_bytes(raw, 0, F32) == 3.5


class TestWicanExpr:
    def test_single_byte(self):
        assert dp.wican_expr(3, U8) == "B3"
        assert dp.wican_expr(3, I8) == "S3"

    def test_big_endian_ranges(self):
        assert dp.wican_expr(3, U16) == "[B3:B4]"
        assert dp.wican_expr(3, I16) == "[S3:S4]"
        assert dp.wican_expr(10, U32) == "[B10:B13]"

    def test_little_endian_unsigned_shift(self):
        assert dp.wican_expr(3, U16, little=True) == "B3 | (B4 << 8)"

    def test_inexpressible_cases_return_none(self):
        assert dp.wican_expr(3, I16, little=True) is None   # LE signed
        assert dp.wican_expr(3, F32) is None                # float


class TestTransforms:
    def test_raw_is_identity(self):
        assert dp.apply_transform([1, 2, 3], "raw") == [1, 2, 3]

    def test_delta(self):
        assert dp.apply_transform([1, 3, 6], "delta") == [0.0, 2, 3]

    def test_abs(self):
        assert dp.apply_transform([-1, 2, -3], "abs") == [1, 2, 3]

    def test_cumsum(self):
        assert dp.apply_transform([1, 2, 3], "cumsum") == [1.0, 3.0, 6.0]

    def test_normalize(self):
        assert dp.apply_transform([0, 5, 10], "normalize") == [0.0, 0.5, 1.0]

    def test_smooth_preserves_length(self):
        out = dp.apply_transform([1, 2, 3, 4, 5], "smooth")
        assert len(out) == 5

    def test_empty(self):
        assert dp.apply_transform([], "delta") == []


class TestRendering:
    def test_norm01(self):
        assert dp._norm01([0, 5, 10]) == [0.0, 0.5, 1.0]
        assert dp._norm01([7, 7]) == [0.0, 0.0]   # zero span -> all 0

    def test_pci_positions(self):
        # 6101ABCD -> frame 04 61 01 AB CD; only B0 is PCI.
        assert dp._pci_positions("6101ABCD") == {0}

    def test_render_plot_shape(self):
        lines = dp.render_plot([1, 2, 3, 2, 1], width=20, height=6)
        assert len(lines) == 8            # height + axis + caption
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
        v = dp.interpret_bytes(nan_bytes, 0, ("f32", 4, "float", True))
        assert v is not None and math.isnan(v)
        inf_bytes = bytes([0x7F, 0x80, 0x00, 0x00])  # +Inf
        assert dp.interpret_bytes(inf_bytes, 0, ("f32", 4, "float", True)) == math.inf

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
        lines = dp._info_lines("MCU", "2102", _VIEW_CAPS, i0=40, total=332,
                               ts_range="2026-07-19 → 2026-07-20", max_rows=20)
        body = "\n".join(lines)
        assert "captures in view" in body
        assert "launch" in body and "driving" in body
        assert "hard accel regen" in body          # notes flattened
        assert "a.yaml" in body and "b.yaml" in body
        assert "40" in body and "41" in body        # global indices (i0 + n)

    def test_info_lines_truncates(self):
        many = [dict(_VIEW_CAPS[0], label=f"c{i}") for i in range(50)]
        lines = dp._info_lines("MCU", "2102", many, 0, 50, "r", max_rows=10)
        assert any("and 40 more" in ln for ln in lines)


class TestOverlayCycle:
    def test_cycles_through_all_and_wraps(self):
        cyc = [None, "A", "B"]
        assert dp._cycle_overlay(None, cyc) == "A"    # off -> first param
        assert dp._cycle_overlay("A", cyc) == "B"
        assert dp._cycle_overlay("B", cyc) is None     # wraps back to off

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
