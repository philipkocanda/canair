"""Support-map bytes on screen: the muted colour, its plumbing into the monitor, the legend."""

from typing import ClassVar

import pytest
from textual.app import App
from textual.binding import Binding

from canair.live._monitor_render import MONITOR_LEGEND, _render_results
from canair.live.multi_batch import _decode_pid_result
from canair.render.formatting import (
    _HIGHLIGHT_STYLE,
    BYTE_COLOUR_LEGEND,
    MAP_BYTE_STYLE,
    _build_byte_colors,
    _render_hex_line,
    render_byte_rulers,
)
from canair.tui.help import HelpMixin, HelpModal, legend_text
from canair.uds.notation import ByteDisplay, ByteNotation
from canair.uds.support_map import map_byte_indices

# 62 BC03 | map FD EE 3C 73 | data 0A 00 00 00 — WiCAN B10 is the first data byte (index 7).
PAYLOAD = "62BC03FDEE3C730A000000"
MAP = (3, 4, 5, 6)
DOOR = ("DOOR", 0, "", "B10:0", None, True)
DECL = {"at": 0, "length": 4, "unit": "bit", "order": "msb"}


def _byte_styles(text) -> dict[str, list[str]]:
    """``{hex byte: [styles]}`` for the byte cells of a rendered hex line."""
    out: dict[str, list[str]] = {}
    for span in text._spans:
        out.setdefault(text.plain[span.start : span.end], []).append(str(span.style))
    return out


class TestMapByteIndices:
    def test_a_service_22_map_sits_after_the_did_echo(self):
        assert map_byte_indices({"support_map": DECL}, "22BC03") == MAP

    def test_a_service_21_map_sits_after_the_one_byte_echo(self):
        decl = {"support_map": {"at": 0, "length": 4, "unit": "byte"}}
        assert map_byte_indices(decl, "2101") == (2, 3, 4, 5)

    def test_further_bitmaps_of_a_field_map_are_included(self):
        decl = {"support_map": {"at": 0, "length": 4, "unit": "field", "also_at": [48]}}
        assert map_byte_indices(decl, "21F2") == (2, 3, 4, 5, 50, 51, 52, 53)

    def test_offset_and_length_move_it(self):
        decl = {"support_map": {"at": 1, "length": 2, "unit": "bit"}}
        assert map_byte_indices(decl, "22B004") == (4, 5)

    @pytest.mark.parametrize("pid_def", [None, {}, {"support_map": {"at": 0}}, {"support_map": 3}])
    def test_none_or_malformed_shows_nothing(self, pid_def):
        assert map_byte_indices(pid_def, "22BC03") == ()


class TestColours:
    def test_an_uncovered_map_byte_is_muted(self):
        colours = _build_byte_colors([DOOR], 11, MAP)
        assert colours[3:7] == [MAP_BYTE_STYLE] * 4
        assert colours[7] == "green"
        assert colours[0] == "bright_black"  # the SID is not part of the map

    def test_a_signal_on_a_map_byte_keeps_its_colour(self):
        on_map = ("ODD", 0, "", "B5", None, False)
        assert _build_byte_colors([on_map], 11, MAP)[3] == "yellow"

    def test_no_declaration_changes_nothing(self):
        assert _build_byte_colors([DOOR], 11) == _build_byte_colors([DOOR], 11, ())

    def test_the_hex_line_draws_map_bytes_muted(self):
        styles = _byte_styles(_render_hex_line(PAYLOAD, [DOOR], False, map_bytes=MAP))
        for byte in ("FD", "EE", "3C", "73"):
            assert styles[byte] == [MAP_BYTE_STYLE]
        assert styles["0A"] == ["green"]

    def test_a_changed_map_byte_still_flashes(self):
        prev = "62BC03FDEE3C720A000000"
        styles = _byte_styles(
            _render_hex_line(PAYLOAD, [DOOR], False, prev_raw=prev, map_bytes=MAP)
        )
        assert styles["73"] == [_HIGHLIGHT_STYLE[MAP_BYTE_STYLE]]

    def test_a_paramless_payload_marks_its_map_too(self):
        styles = _byte_styles(_render_hex_line(PAYLOAD, [], True, map_bytes=MAP))
        assert styles["FD"] == [MAP_BYTE_STYLE]

    def test_the_ruler_matches_the_hex_line(self):
        display = ByteDisplay(ByteNotation.WICAN, payload_len=11, sub_bytes=2).aligned()
        ruler = render_byte_rulers(display, [DOOR], map_bytes=MAP)
        assert any(MAP_BYTE_STYLE in str(s.style) for s in ruler._spans)


class TestMonitorPlumbing:
    def test_a_decoded_entry_carries_the_map_bytes(self):
        info = {"parameters": {"DOOR": {"expression": "B10:0"}}, "support_map": DECL}
        entry = _decode_pid_result("22BC03", info, False, PAYLOAD, bytes.fromhex(PAYLOAD), None)
        assert entry["map_bytes"] == MAP

    def test_a_pid_without_a_map_carries_none(self):
        info = {"parameters": {"DOOR": {"expression": "B10:0"}}, "support_map": None}
        entry = _decode_pid_result("22BC03", info, False, PAYLOAD, bytes.fromhex(PAYLOAD), None)
        assert "map_bytes" not in entry

    def test_the_monitor_renders_map_bytes_muted(self):
        entry = {"pid": "22BC03", "params": [DOOR], "raw_hex": PAYLOAD, "map_bytes": MAP}
        text = _render_results([("IGPM (0x770)", [entry])], False, 1, 0.1, 1.0, footer=False)
        assert any(
            MAP_BYTE_STYLE == str(s.style) and text.plain[s.start : s.end] == "FD"
            for s in text._spans
        )


class TestLegend:
    def test_every_byte_colour_the_hex_line_uses_is_explained(self):
        legend_styles = {style for _sample, style, _meaning in BYTE_COLOUR_LEGEND}
        assert {"green", "yellow", "bright_black", MAP_BYTE_STYLE} <= legend_styles

    def test_the_monitor_legend_has_the_bytes_and_the_markers(self):
        meanings = " ".join(m for _s, _st, m in MONITOR_LEGEND)
        assert "support map" in meanings and "stale" in meanings

    def test_legend_text_draws_each_sample_in_its_style(self):
        text = legend_text(BYTE_COLOUR_LEGEND, 4)
        assert text.plain.startswith("Colours\n")
        assert any(str(s.style) == MAP_BYTE_STYLE for s in text._spans)

    @pytest.mark.asyncio
    async def test_the_help_modal_shows_the_legend(self):
        class _LegendApp(HelpMixin, App):
            BINDINGS: ClassVar = [Binding("question_mark", "help", "help")]
            HELP_LEGEND = BYTE_COLOUR_LEGEND

        app = _LegendApp()
        async with app.run_test(size=(90, 40)) as pilot:
            await pilot.press("question_mark")
            await pilot.pause(0.05)
            assert isinstance(app.screen, HelpModal)
            assert app.screen.query("#help-legend")
            await pilot.press("escape")

    @pytest.mark.asyncio
    async def test_no_legend_no_block(self):
        class _PlainApp(HelpMixin, App):
            BINDINGS: ClassVar = [Binding("question_mark", "help", "help")]

        app = _PlainApp()
        async with app.run_test(size=(90, 40)) as pilot:
            await pilot.press("question_mark")
            await pilot.pause(0.05)
            assert not app.screen.query("#help-legend")
            await pilot.press("escape")
