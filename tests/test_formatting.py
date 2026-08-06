"""Tests for canlib.formatting — output formatting helpers."""

import pytest

from canlib.formatting import (
    _HIGHLIGHT_STYLE,
    changed_param_highlights,
    format_value,
    param_byte_index_str,
    param_byte_indices,
    print_hexdump,
    render_byte_rulers,
    render_param_table,
)
from canlib.notation import ByteDisplay, ByteNotation


def _display(payload_len: int, notation: ByteNotation = ByteNotation.WICAN, sub_bytes: int = 1):
    return ByteDisplay(notation, payload_len=payload_len, sub_bytes=sub_bytes)


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

    def test_non_numeric_value_does_not_crash(self):
        # A typed/enum label or stray string must render as-is, never raise
        # (this render runs inside the TUI compose(), where a crash is fatal).
        assert format_value("fanMAX", "") == "fanMAX"
        assert format_value("open", "state") == "open state"


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

    def test_byte_column_shown_with_display(self):
        # Rendered in the display's notation — WiCAN by default, so it echoes B09.
        rows = [("X", 1.0, "", "B09", None, True)]
        text = render_param_table(rows, display=_display(27)).plain
        assert text.rstrip().endswith("B9")

    def test_byte_column_follows_notation(self):
        # The same byte named in ISO-TP space (WiCAN B09 → payload index 6).
        rows = [("X", 1.0, "", "B09", None, True)]
        text = render_param_table(rows, display=_display(27, ByteNotation.ISOTP)).plain
        assert text.rstrip().endswith("i6")

    def test_byte_column_absent_without_display(self):
        rows = [("X", 1.0, "", "B09", None, True)]
        text = render_param_table(rows).plain
        # No trailing byte reference — line ends right after the mark.
        assert text.rstrip().endswith("✓")

    def test_byte_column_out_of_range_is_flagged(self):
        # B99 maps past the end of a 5-byte payload → flagged as an anomaly.
        rows = [("X", 1.0, "", "B99", None, True)]
        text = render_param_table(rows, display=_display(5)).plain
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


class TestParamByteIndexStr:
    def test_valid_run_in_wican(self):
        # Default notation echoes the expression's own space.
        assert param_byte_index_str("[B18:B19]", _display(27)) == "B18-B19"

    def test_valid_run_in_isotp(self):
        assert param_byte_index_str("[B18:B19]", _display(27, ByteNotation.ISOTP)) == "i14-i15"

    def test_discontiguous_run_is_comma_joined(self):
        assert param_byte_index_str("B9 + B12", _display(27)) == "B9,B12"

    def test_pci_byte_flagged(self):
        # WiCAN B16 is a consecutive-frame PCI byte → no payload position.
        assert param_byte_index_str("B16/2", _display(27)) == "⚠B16"

    def test_out_of_range_flagged(self):
        assert param_byte_index_str("B99", _display(5)) == "⚠B99"

    def test_mixed_valid_and_anomalous(self):
        # B9 → payload index 6 (valid), B16 → PCI (anomaly)
        assert param_byte_index_str("B9 + B16", _display(27)) == "B9 ⚠B16"

    def test_empty_expression(self):
        assert param_byte_index_str("", _display(27)) == ""


class TestChangedParamHighlights:
    # 5-byte single-frame payload: WiCAN B3 → ISO-TP/ELM index 2.
    ROWS = (("SOC", 50.0, "%", "B3", None, True),)

    def test_no_prev_no_highlight(self):
        assert changed_param_highlights(list(self.ROWS), "6201020304", "") == {}

    def test_unchanged_byte_no_highlight(self):
        # Byte 2 (the one B3 reads) is identical between the two payloads.
        assert changed_param_highlights(list(self.ROWS), "6201020304", "6201029904") == {}

    def test_changed_byte_highlights_owning_param(self):
        # Byte 2 differs (02 → 99); B3 reads it → SOC lights up, verified variant.
        out = changed_param_highlights(list(self.ROWS), "6201990304", "6201020304")
        assert out == {"SOC": _HIGHLIGHT_STYLE["green"]}

    def test_unverified_uses_yellow_variant(self):
        rows = [("UNK", 1.0, "", "B3", None, False)]
        out = changed_param_highlights(rows, "6201990304", "6201020304")
        assert out == {"UNK": _HIGHLIGHT_STYLE["yellow"]}

    def test_change_outside_param_bytes_ignored(self):
        # Byte 4 changes but B3 reads byte 2 → not highlighted.
        assert changed_param_highlights(list(self.ROWS), "6201020399", "6201020304") == {}

    def test_error_and_exprless_params_skipped(self):
        rows = [
            ("BAD", None, "", "B3", "boom", True),
            ("NOEXPR", 1.0, "", "", None, True),
        ]
        assert changed_param_highlights(rows, "6201990304", "6201020304") == {}

    def test_decoded_value_unchanged_not_highlighted(self):
        # Byte 2 changed (02→99) and B3 reads it, but the decoded SOC value is the
        # same as the prior cycle → no highlight (e.g. sub-resolution / bitfield
        # change that rounds to the same value).
        out = changed_param_highlights(
            list(self.ROWS), "6201990304", "6201020304", prev_values={"SOC": 50.0}
        )
        assert out == {}

    def test_decoded_value_changed_highlighted(self):
        # Byte 2 changed and the decoded SOC value moved → highlight.
        out = changed_param_highlights(
            list(self.ROWS), "6201990304", "6201020304", prev_values={"SOC": 40.0}
        )
        assert out == {"SOC": _HIGHLIGHT_STYLE["green"]}

    def test_no_prev_values_falls_back_to_raw_change(self):
        # With no prior decoded snapshot, the raw-byte-change heuristic still
        # highlights (first-change behaviour).
        out = changed_param_highlights(
            list(self.ROWS), "6201990304", "6201020304", prev_values=None
        )
        assert out == {"SOC": _HIGHLIGHT_STYLE["green"]}


def _row_style_at(text, needle: str) -> str:
    """Return the style string covering the first char of ``needle`` in ``text``."""
    plain = text.plain
    idx = plain.index(needle)
    for span in text.spans:
        if span.start <= idx < span.end:
            return str(span.style)
    return ""


class TestRenderParamTableHighlight:
    ROWS = (("SOC", 50.0, "%", "B3", None, True),)

    def test_changed_style_applied_to_row(self):
        styles = {"SOC": _HIGHLIGHT_STYLE["green"]}
        text = render_param_table(list(self.ROWS), changed_styles=styles)
        assert _HIGHLIGHT_STYLE["green"] in _row_style_at(text, "SOC")

    def test_selection_beats_change_highlight(self):
        styles = {"SOC": _HIGHLIGHT_STYLE["green"]}
        text = render_param_table(list(self.ROWS), selected_name="SOC", changed_styles=styles)
        # The reverse selection style wins on a row that is both selected + changed.
        assert "reverse" in _row_style_at(text, "SOC")

    def test_no_changed_styles_leaves_row_plain(self):
        text = render_param_table(list(self.ROWS))
        assert "on dark_green" not in _row_style_at(text, "SOC")


class TestRenderByteRulers:
    """One ruler row, in the caller's notation — never two competing spaces."""

    def test_single_row_labelled_with_its_notation(self):
        lines = render_byte_rulers(_display(27), [], prefix_width=8).plain.splitlines()
        assert len(lines) == 1
        assert lines[0].split()[0] == "wican"

    def test_wican_row_skips_pci(self):
        # 27-byte payload: ISO-TP 6 → WiCAN 9 (skips PCI byte 8).
        cells = render_byte_rulers(_display(27), [], prefix_width=8).plain.splitlines()[0].split()
        assert cells[1] == "02"  # ISO-TP 0 → WiCAN 2
        assert cells[7] == "09"  # ISO-TP 6 → WiCAN 9 (8 is PCI)

    def test_isotp_row_is_sequential(self):
        display = _display(5, ByteNotation.ISOTP)
        cells = render_byte_rulers(display, [], prefix_width=8).plain.splitlines()[0].split()
        assert cells == ["isotp", "00", "01", "02", "03", "04"]

    def test_cells_start_at_prefix_width(self):
        line = render_byte_rulers(_display(10), [], prefix_width=16).plain.splitlines()[0]
        assert line.index("02") == 16

    @pytest.mark.parametrize("notation", list(ByteNotation))
    def test_every_notation_keeps_two_char_columns(self, notation):
        # A ruler must align under the hex bytes; bix (a *bit* index) can't, so
        # ByteDisplay.aligned() falls back rather than skewing the columns.
        display = ByteDisplay(notation, payload_len=27, sub_bytes=2).aligned()
        line = render_byte_rulers(display, [], prefix_width=8).plain.splitlines()[0]
        # prefix + one 2-char cell per byte, single-space separated.
        assert len(line) == 8 + 27 * 3 - 1


class TestPrintHexdump:
    """`print_hexdump` labels depend on which byte space the buffer is in.

    Regression: an ISO-TP payload and a WiCAN-layout frame are both plain
    `bytes`, and they differ by the PCI framing bytes. `canair repl`'s `!hexdump`
    passed a WiCAN frame to the ISO-TP path, so every `Bnn` label was shifted by
    the framing offset — with no type error to catch it.
    """

    HEX = "6101ABCDEF0102030405060708090A"

    def _labels(self, capsys) -> list[str]:
        out = capsys.readouterr().out
        return [tok for line in out.splitlines() if "Bnn:" in line for tok in line.split()[1:]]

    def test_isotp_payload_labels_reinsert_pci_offsets(self, capsys):
        # ISO-TP index 6 -> WiCAN B09 (B08 is the first consecutive-frame PCI byte).
        print_hexdump(bytes.fromhex(self.HEX))
        labels = self._labels(capsys)
        assert labels[:6] == ["B02", "B03", "B04", "B05", "B06", "B07"]
        assert labels[6] == "B09", "PCI byte B08 must be skipped"

    def test_wican_frame_labels_are_the_identity(self, capsys):
        from canlib.autopid_layout import uds_hex_to_wican_bytes

        print_hexdump(uds_hex_to_wican_bytes(self.HEX), layout="wican")
        labels = self._labels(capsys)
        assert labels[:4] == ["B00", "B01", "B02", "B03"]
        # No re-insertion: the buffer already contains the framing bytes.
        assert labels[8] == "B08"

    def test_both_layouts_agree_on_where_a_data_byte_lives(self, capsys):
        """The same physical byte must carry the same label in either view."""
        from canlib.autopid_layout import uds_hex_to_wican_bytes

        payload = bytes.fromhex(self.HEX)
        first_data = payload.index(0xAB)  # first byte after SID + PID echo

        print_hexdump(payload)
        isotp_label = self._labels(capsys)[first_data]

        frame = uds_hex_to_wican_bytes(self.HEX)
        print_hexdump(frame, layout="wican")
        wican_label = self._labels(capsys)[frame.index(0xAB)]

        assert isotp_label == wican_label == "B04"
