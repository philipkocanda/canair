"""Layout of the discover and identity reports (:mod:`canair.live.discover_render`)."""

from __future__ import annotations

import io
import re

from canair.live.discover_render import POSITIVE, REFUSED, status_cell
from canair.render import ansi
from canair.render.paint import Painter, column_widths, render_row

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

ROWS = [
    [POSITIVE, ("0x778", ""), ("IGPM", ansi.raw.BOLD), status_cell("positive"), ("7E00", "")],
    [REFUSED, ("0x74E", ""), ("Unknown-746", ""), status_cell("NRC 0x22"), ("7F3E22", "")],
]


def _render(monkeypatch, force: bool) -> list[str]:
    monkeypatch.delenv("NO_COLOR", raising=False)
    if force:
        monkeypatch.setenv("FORCE_COLOR", "1")
    else:
        monkeypatch.delenv("FORCE_COLOR", raising=False)
    paint = Painter(io.StringIO())
    widths = column_widths(ROWS)
    return [render_row(row, widths, paint) for row in ROWS]


def test_columns_line_up(monkeypatch):
    plain = _render(monkeypatch, force=False)
    assert plain == [
        "    +  0x778  IGPM         positive  7E00",
        "    ~  0x74E  Unknown-746  NRC 0x22  7F3E22",
    ]


def test_colour_does_not_move_the_columns(monkeypatch):
    coloured = _render(monkeypatch, force=True)
    assert any("\x1b[" in line for line in coloured)
    assert [_ANSI.sub("", line) for line in coloured] == _render(monkeypatch, force=False)


def test_no_colour_for_a_pipe(monkeypatch):
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    paint = Painter(io.StringIO())
    assert paint("x", ansi.raw.RED) == "x"
    assert paint.heading("Responders", "21:06:07", "") == "\n  Responders · 21:06:07"


def test_fields_align_their_values(monkeypatch):
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    paint = Painter(io.StringIO())
    assert paint.fields([("request", "3E00"), ("mode", "normal_11bit")]) == [
        "    request  3E00",
        "    mode     normal_11bit",
    ]


def test_status_colours():
    assert status_cell("positive")[1] == ansi.raw.GREEN
    assert status_cell("NRC 0x7F")[1] == ansi.raw.YELLOW
    assert status_cell("pending")[1] == ansi.raw.RED
