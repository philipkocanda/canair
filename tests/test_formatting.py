"""Tests for canlib.formatting — output formatting helpers."""

from canlib.formatting import (
    format_byte_ranges,
    format_value,
    param_byte_index_str,
    param_byte_indices,
    render_byte_rulers,
    render_param_table,
)


class TestFormatValue:
    def test_integer_value(self):
        assert format_value(42.0, "km/h") == "42 km/h"

    def test_float_value(self):
        assert format_value(3.14, "V") == "3.14 V"

    def test_no_unit(self):
        assert format_value(100.0, "") == "100"

    def test_zero(self):
        assert format_value(0.0, "%") == "0 %"

    def test_negative(self):
        assert format_value(-40.0, "°C") == "-40 °C"

    def test_small_decimal(self):
        assert format_value(12.50, "A") == "12.50 A"


class TestRenderParamTable:
    def test_empty_returns_empty_text(self):
        t = render_param_table([])
        assert t.plain == ""

    def test_name_value_and_verified_mark(self):
        rows = [("SOC", 50.0, "%", "B09/2", None, True)]
        text = render_param_table(rows).plain
        assert "SOC" in text
        assert "50 %" in text
        assert "✓" in text

    def test_unverified_mark(self):
        rows = [("UNK", 1.0, "", "B03", None, False)]
        assert "?" in render_param_table(rows).plain

    def test_error_row(self):
        rows = [("BAD", None, "", "B99", "division by zero", False)]
        text = render_param_table(rows).plain
        assert "BAD" in text
        assert "ERROR: division by zero" in text

    def test_verbose_shows_expression(self):
        rows = [("SOC", 50.0, "%", "B09/2", None, True)]
        assert "B09/2" in render_param_table(rows, verbose=True).plain
        assert "B09/2" not in render_param_table(rows, verbose=False).plain

    def test_display_field(self):
        rows = [("PREHEAT", 480.0, "min", "B07", None, True, "f'{int(v)//60:02d}:{int(v)%60:02d}'")]
        assert "480 min (08:00)" in render_param_table(rows).plain

    def test_columns_aligned(self):
        rows = [
            ("A", 1.0, "", "B0", None, True),
            ("LONGER_NAME", 2.0, "", "B1", None, True),
        ]
        lines = [ln for ln in render_param_table(rows).plain.splitlines() if ln.strip()]
        # Value column starts at the same offset regardless of name length.
        assert lines[0].index("1") == lines[1].index("2")

    def test_custom_indent(self):
        rows = [("A", 1.0, "", "B0", None, True)]
        assert render_param_table(rows, indent="::").plain.startswith("::A")

    def test_byte_column_shown_with_n_bytes(self):
        # 27-byte payload → WiCAN B09 maps to ISO-TP/ELM index 6.
        rows = [("X", 1.0, "", "B09", None, True)]
        text = render_param_table(rows, n_bytes=27).plain
        assert text.rstrip().endswith(" 6")

    def test_byte_column_absent_without_n_bytes(self):
        rows = [("X", 1.0, "", "B09", None, True)]
        text = render_param_table(rows).plain
        # No trailing byte index — line ends right after the mark.
        assert text.rstrip().endswith("✓")

    def test_byte_column_out_of_range_is_blank(self):
        # B99 maps past the end of a 5-byte payload → flagged as an anomaly.
        rows = [("X", 1.0, "", "B99", None, True)]
        text = render_param_table(rows, n_bytes=5).plain
        assert "⚠B99" in text


class TestParamByteIndices:
    def test_single_byte_singleframe(self):
        # payload_len ≤ 7 → single frame, elm = wican - 1
        assert param_byte_indices("B3", 5) == [2]

    def test_multibyte_range_multiframe(self):
        # 27-byte payload: [B18:B19] → ISO-TP indices 14, 15
        assert param_byte_indices("[B18:B19]/100", 27) == [14, 15]

    def test_out_of_range_dropped(self):
        assert param_byte_indices("B99", 5) == []

    def test_bit_access_maps_to_byte(self):
        # B9:0 (bit 0 of WiCAN byte 9) → ISO-TP/ELM index 6.
        assert param_byte_indices("B9:0", 27) == [6]

    def test_pci_byte_dropped(self):
        # WiCAN B8 is a consecutive-frame PCI byte → no ELM mapping.
        assert param_byte_indices("B8:0", 27) == []


class TestFormatByteRanges:
    def test_empty(self):
        assert format_byte_ranges([]) == ""

    def test_single(self):
        assert format_byte_ranges([7]) == "7"

    def test_contiguous_run(self):
        assert format_byte_ranges([3, 4, 5]) == "3-5"

    def test_mixed(self):
        assert format_byte_ranges([3, 4, 5, 9, 11, 12]) == "3-5,9,11-12"


class TestParamByteIndexStr:
    def test_valid_only(self):
        assert param_byte_index_str("[B18:B19]", 27) == "14-15"

    def test_pci_byte_flagged(self):
        # WiCAN B16 is a consecutive-frame PCI byte → no payload position.
        assert param_byte_index_str("B16/2", 27) == "⚠B16"

    def test_out_of_range_flagged(self):
        assert param_byte_index_str("B99", 5) == "⚠B99"

    def test_mixed_valid_and_anomalous(self):
        # B9 → ELM 6 (valid), B16 → PCI (anomaly)
        assert param_byte_index_str("B9 + B16", 27) == "6 ⚠B16"

    def test_empty_expression(self):
        assert param_byte_index_str("", 27) == ""


class TestRenderByteRulers:
    def test_two_rows_idx_and_wican(self):
        text = render_byte_rulers(27, [], prefix_width=8).plain
        lines = text.splitlines()
        assert lines[0].split()[0] == "idx"
        assert lines[1].split()[0] == "wican"

    def test_idx_row_is_sequential(self):
        lines = render_byte_rulers(5, [], prefix_width=8).plain.splitlines()
        assert lines[0].split()[1:] == ["00", "01", "02", "03", "04"]

    def test_wican_row_skips_pci(self):
        # 27-byte payload: ISO-TP 6 → WiCAN 9 (skips PCI byte 8).
        wican = render_byte_rulers(27, [], prefix_width=8).plain.splitlines()[1].split()[1:]
        assert wican[0] == "02"  # ISO-TP 0 → WiCAN 2
        assert wican[6] == "09"  # ISO-TP 6 → WiCAN 9 (8 is PCI)

    def test_columns_align_across_rows(self):
        lines = render_byte_rulers(10, [], prefix_width=16).plain.splitlines()
        # Both rows start their numbers at the same column (prefix_width).
        assert lines[0].index("00") == lines[1].index("02") == 16
