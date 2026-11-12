"""The progress line of ``memory dump`` (canair.live.memory.dump)."""

from __future__ import annotations

import io
import re
from types import SimpleNamespace

import pytest

from canair.live.memory.dump import BAR_CELLS, DumpMeter, LineState, render_line
from canair.render.paint import Painter
from canair.render.progress import ProgressLine, visible_length

PLAIN = Painter(io.StringIO())  # not a terminal: no colour codes


def _state(**kw) -> LineState:
    base = {
        "done": 49_562,
        "total": 846_016,
        "address": 0x16000,
        "range_index": 3,
        "range_count": 6,
        "block": 61,
        "rate": 188.0,
        "eta": 4200.0,
    }
    return LineState(**{**base, **kw})


def test_a_wide_terminal_shows_everything():
    line = render_line(_state(retried=2), PLAIN, 200)
    assert line == (
        "  █░░░░░░░░░░░░░░░   5.9 % · 48.4 KiB / 826.2 KiB · 188 B/s · ~1 h 10 min left"
        " · range 3/6 0x016000 · block 61 · 2 retried"
    )


def test_a_single_range_does_not_say_range_1_of_1():
    line = render_line(_state(range_index=1, range_count=1), PLAIN, 200)
    assert "range" not in line and "0x016000" in line


@pytest.mark.parametrize("columns", [30, 50, 80, 100, 120])
def test_the_line_never_reaches_the_last_column(columns):
    line = render_line(_state(retried=12), PLAIN, columns)
    assert visible_length(line) <= columns - 1


def test_narrow_terminals_drop_the_least_useful_segments_first():
    line = render_line(_state(), PLAIN, 80)
    assert "5.9 %" in line and "left" in line and "188 B/s" in line
    assert "block" not in line


def test_the_percentage_is_never_dropped():
    assert "5.9 %" in render_line(_state(), PLAIN, 12)


def test_no_speed_or_eta_until_they_are_known():
    line = render_line(_state(rate=None, eta=None), PLAIN, 200)
    assert "B/s" not in line and "left" not in line


def test_a_full_bar_at_the_end_and_an_empty_one_at_the_start():
    line = render_line(_state(done=846_016), PLAIN, 200)
    assert "█" * BAR_CELLS in line and "100.0 %" in line
    assert "░" * BAR_CELLS in render_line(_state(done=0), PLAIN, 200)


def test_an_unfinished_bar_always_shows_its_gap():
    assert "█" * BAR_CELLS not in render_line(_state(done=846_000), PLAIN, 200)


def test_colour_is_added_without_changing_the_text(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    painted = render_line(_state(retried=1), Painter(io.StringIO()), 200)
    assert "\x1b[" in painted
    assert re.sub(r"\x1b\[[0-9;]*m", "", painted) == render_line(_state(retried=1), PLAIN, 200)
    assert "\x1b[93m1 retried" in painted  # a warning is yellow, and also says the word


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _meter(ranges, delivered=0):
    reader = SimpleNamespace(delivered=delivered, block=61, retries=0)
    clock = _Clock()
    stream = _Tty()
    meter = DumpMeter(ranges, reader, line=ProgressLine(stream), clock=clock, columns=lambda: 200)
    return meter, reader, clock, stream


def test_progress_counts_the_whole_dump_not_the_range():
    meter, reader, _clock, _ = _meter([(0x1000, 0x1FFF), (0x3000, 0x4FFF)])
    meter.start_range(1)
    meter.extent(0x3000)
    assert meter.total == 0x3000 and meter.done == 0x1000  # the first range is behind it
    reader.delivered = 0x800
    assert meter.done == 0x1800


def test_a_refused_extent_still_counts_as_passed():
    meter, _reader, _, _ = _meter([(0x0, 0x1FFF)])
    meter.extent(0x0)  # nothing was delivered for it
    meter.extent(0x1000)
    assert meter.done == 0x1000


def test_the_line_names_the_range_and_gains_speed_and_time_left():
    meter, reader, clock, stream = _meter([(0x0, 0x3FFF)])
    meter.extent(0x0)
    clock.now, reader.delivered = 10.0, 1000
    meter.show()
    out = stream.getvalue()
    assert "100 B/s" in out and "left" in out and "6.1 %" in out


def test_redraws_are_rate_limited_but_an_extent_always_draws():
    meter, _reader, clock, stream = _meter([(0x0, 0x3FFF)])
    meter.extent(0x0)
    drawn = stream.getvalue().count("\r")
    for _ in range(50):
        clock.now += 0.001
        meter.show()
    assert stream.getvalue().count("\r") == drawn
    meter.extent(0x1000)
    assert stream.getvalue().count("\r") == drawn + 1


def test_nothing_is_drawn_off_a_terminal():
    reader = SimpleNamespace(delivered=0, block=61, retries=0)
    stream = io.StringIO()
    meter = DumpMeter([(0, 0xFFF)], reader, line=ProgressLine(stream), columns=lambda: 80)
    meter.extent(0)
    meter.show(force=True)
    assert stream.getvalue() == ""
