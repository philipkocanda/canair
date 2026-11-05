"""Tests for the canair sniff table."""

from canair.analysis.sniff_stats import SniffStats
from canair.render.sniff import render_sniff_table


def test_render_does_not_crash():
    from rich.console import Console

    s = SniffStats()
    s.record(0x7E4, bytes.fromhex("0201FF"), ts=0.0, extended=False)
    s.record(0x18DAF110, b"\xaa\xbb", ts=0.0, extended=True)
    table = render_sniff_table(s.snapshot())
    assert table.row_count == 2
    console = Console(width=80)
    with console.capture() as cap:
        console.print(table)
    out = cap.get()
    assert "7E4" in out and "18DAF110" in out


def _byte_styles(table) -> dict[str, str]:
    """Each data byte of the first row with the style it was drawn in."""
    cell = table.columns[3]._cells[0]
    return {cell.plain[span.start : span.end]: str(span.style) for span in cell.spans}


def test_a_changed_byte_has_the_style_the_monitor_gives_it():
    from canair.render.formatting import _HIGHLIGHT_STYLE
    from canair.render.sniff import CHANGED_BYTE_STYLE

    s = SniffStats()
    s.record(0x7E4, bytes.fromhex("0201FF"), ts=0.0, extended=False)
    s.record(0x7E4, bytes.fromhex("0201FE"), ts=1.0, extended=False)
    styles = _byte_styles(render_sniff_table(s.snapshot()))
    assert CHANGED_BYTE_STYLE == _HIGHLIGHT_STYLE["green"]
    assert styles == {"FE": CHANGED_BYTE_STYLE}


def test_an_unchanged_byte_keeps_the_terminal_default_colour():
    """A fixed `white` is unreadable on a light terminal; the default colour is not."""
    s = SniffStats()
    s.record(0x7E4, bytes.fromhex("0201FF"), ts=0.0, extended=False)
    s.record(0x7E4, bytes.fromhex("0201FE"), ts=1.0, extended=False)
    cell = render_sniff_table(s.snapshot()).columns[3]._cells[0]
    assert len(cell.spans) == 1  # only the changed byte is styled


def test_the_legend_names_the_changed_byte_style():
    from canair.render.sniff import CHANGED_BYTE_STYLE, SNIFF_LEGEND

    assert any(style == CHANGED_BYTE_STYLE for _s, style, _m in SNIFF_LEGEND)
