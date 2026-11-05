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
