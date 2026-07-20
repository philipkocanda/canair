"""Tests for canlib.tui — terminal/text-UI helpers (scroll, keys, framing)."""

from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from canlib.tui import (
    ScrollView,
    compose_frame,
    decode_key,
    raw_screen,
    segments_to_str,
    wrap_text_lines,
)


def _plain_console(width: int = 40) -> Console:
    """A Console that renders to a buffer with no ANSI (deterministic asserts)."""
    return Console(file=StringIO(), width=width, color_system=None, highlight=False)


class TestDecodeKey:
    def test_arrows(self):
        assert decode_key("\x1b[A") == "UP"
        assert decode_key("\x1b[B") == "DOWN"
        assert decode_key("\x1b[C") == "RIGHT"
        assert decode_key("\x1b[D") == "LEFT"

    def test_ss3_arrow_variant(self):
        assert decode_key("\x1bOA") == "UP"

    def test_paging(self):
        assert decode_key("\x1b[5~") == "PGUP"
        assert decode_key("\x1b[6~") == "PGDN"

    def test_home_end_variants(self):
        for seq in ("\x1b[H", "\x1bOH", "\x1b[1~", "\x1b[7~"):
            assert decode_key(seq) == "HOME"
        for seq in ("\x1b[F", "\x1bOF", "\x1b[4~", "\x1b[8~"):
            assert decode_key(seq) == "END"

    def test_control_keys(self):
        assert decode_key("\r") == "ENTER"
        assert decode_key("\n") == "ENTER"
        assert decode_key("\x7f") == "BACKSPACE"
        assert decode_key("\x08") == "BACKSPACE"
        assert decode_key("\x03") == "CTRL_C"
        assert decode_key("\x1b") == "ESC"

    def test_passthrough_printable(self):
        for ch in ("q", "j", "k", "g", "G", "f"):
            assert decode_key(ch) == ch

    def test_unknown_sequence_passthrough(self):
        assert decode_key("\x1b[99Z") == "\x1b[99Z"


class TestScrollView:
    def test_follow_pins_to_bottom(self):
        sv = ScrollView(follow=True)
        sv.update(total=100, viewport=10)
        assert sv.top == 90
        assert sv.bottom == 100

    def test_follow_tracks_growing_content(self):
        sv = ScrollView(follow=True)
        sv.update(total=20, viewport=10)
        assert sv.top == 10
        sv.update(total=25, viewport=10)  # content grew while following
        assert sv.top == 15

    def test_scroll_up_detaches_follow(self):
        sv = ScrollView(follow=True)
        sv.update(total=100, viewport=10)
        sv.scroll(-5)
        assert sv.follow is False
        assert sv.top == 85

    def test_scroll_up_then_content_grows_stays_put(self):
        sv = ScrollView(follow=True)
        sv.update(total=100, viewport=10)
        sv.scroll(-5)  # top=85, detached
        sv.update(total=140, viewport=10)  # more content arrives
        assert sv.top == 85  # stays where the user parked it

    def test_scroll_to_bottom_reattaches_follow(self):
        sv = ScrollView(follow=True)
        sv.update(total=100, viewport=10)
        sv.scroll(-5)
        assert sv.follow is False
        sv.scroll(5)  # back to bottom
        assert sv.top == 90
        assert sv.follow is True

    def test_scroll_clamps_at_top(self):
        sv = ScrollView(follow=True)
        sv.update(total=100, viewport=10)
        sv.scroll(-999)
        assert sv.top == 0

    def test_page(self):
        sv = ScrollView(follow=False)
        sv.update(total=100, viewport=10)
        sv.top = 0
        sv.page(1)  # down one page, viewport-1 overlap
        assert sv.top == 9

    def test_home_and_end(self):
        sv = ScrollView(follow=True)
        sv.update(total=100, viewport=10)
        sv.home()
        assert sv.top == 0 and sv.follow is False
        sv.end()
        assert sv.top == 90 and sv.follow is True

    def test_toggle_follow(self):
        sv = ScrollView(follow=False)
        sv.update(total=100, viewport=10)
        sv.top = 20
        sv.toggle_follow()
        assert sv.follow is True and sv.top == 90
        sv.toggle_follow()
        assert sv.follow is False

    def test_content_shorter_than_viewport(self):
        sv = ScrollView(follow=True)
        sv.update(total=3, viewport=10)
        assert sv.max_top == 0
        assert sv.top == 0
        assert sv.bottom == 3


class TestWrapAndSegments:
    def test_line_count_matches_content(self):
        con = _plain_console(width=40)
        t = Text()
        for i in range(5):
            t.append(f"row{i}\n")
        lines = wrap_text_lines(t, 40, con)
        # 5 explicit newlines -> 5 content rows + 1 trailing empty row
        assert len(lines) == 6

    def test_wrapping_increases_line_count(self):
        con = _plain_console(width=10)
        t = Text("x" * 25)  # 25 chars at width 10 -> 3 rows
        lines = wrap_text_lines(t, 10, con)
        assert len(lines) == 3

    def test_segments_to_str_roundtrip(self):
        con = _plain_console(width=40)
        t = Text("hello\nworld\n")
        lines = wrap_text_lines(t, 40, con)
        out = segments_to_str(con, lines)
        assert "hello" in out
        assert "world" in out


class TestComposeFrame:
    def test_contains_clear_and_footer(self):
        con = _plain_console(width=40)
        body = Text("\n".join(f"line{i}" for i in range(50)) + "\n")
        sv = ScrollView(follow=True)
        frame = compose_frame(con, body, sv, width=40, height=12, footer=Text("STATUS"))
        assert frame.startswith("\033[2J\033[H")
        assert "STATUS" in frame

    def test_follow_shows_tail_not_head(self):
        con = _plain_console(width=40)
        body = Text("\n".join(f"line{i}" for i in range(50)) + "\n")
        sv = ScrollView(follow=True)
        frame = compose_frame(con, body, sv, width=40, height=12, footer=Text("F"))
        # viewport = height - footer_rows(1) - 1 = 10; tail should be visible
        assert "line49" in frame
        assert "line0\n" not in frame

    def test_home_shows_head_not_tail(self):
        con = _plain_console(width=40)
        body = Text("\n".join(f"line{i}" for i in range(50)) + "\n")
        sv = ScrollView(follow=True)
        compose_frame(con, body, sv, width=40, height=12, footer=Text("F"))
        sv.home()
        frame = compose_frame(con, body, sv, width=40, height=12, footer=Text("F"))
        assert "line0" in frame
        assert "line49" not in frame

    def test_viewport_limits_visible_rows(self):
        con = _plain_console(width=40)
        body = Text("\n".join(f"line{i}" for i in range(50)) + "\n")
        sv = ScrollView(follow=True)
        compose_frame(con, body, sv, width=40, height=12, footer=Text("F"))
        assert sv.viewport == 10  # 12 - 1 footer - 1 cushion

    def test_footer_cropped_to_one_row(self):
        con = _plain_console(width=10)
        body = Text("short\n")
        sv = ScrollView()
        # A footer that would wrap to several rows must be cropped to footer_rows.
        long_footer = Text("A" * 100)
        frame = compose_frame(con, body, sv, width=10, height=12, footer=long_footer, footer_rows=1)
        # Only one footer row -> at most one 10-char run of A's after the body.
        after_body = frame.split("short")[-1]
        assert "A" * 10 in after_body
        assert "A" * 11 not in after_body

    def test_callable_footer_sees_updated_scroll(self):
        # A callable footer is evaluated after the scroll offset is set for this
        # frame, so it can report accurate position on the very first draw.
        con = _plain_console(width=40)
        body = Text("\n".join(f"line{i}" for i in range(50)) + "\n")
        sv = ScrollView(follow=True)

        def footer(s: ScrollView) -> Text:
            return Text(f"{s.top + 1}-{s.bottom}/{s.total}")

        frame = compose_frame(con, body, sv, width=40, height=12, footer=footer)
        # 51 lines, viewport 10, following -> top=41 -> "42-51/51"
        assert "42-51/51" in frame


class TestRawScreenNonTTY:
    @pytest.mark.asyncio
    async def test_non_tty_get_key_returns_none(self):
        # Under pytest stdin is not a TTY; raw_screen must degrade gracefully.
        async with raw_screen(alt_screen=False, hide_cursor=False) as get_key:
            assert await get_key(timeout=0.01) is None
