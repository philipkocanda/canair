"""Shared terminal / text-UI helpers.

Reusable building blocks for the interactive, full-screen modes (live monitor,
IOControl TUI, routines TUI, capture stepper). Centralises the terminal
plumbing that was previously copy-pasted across modules:

* ``terminal_lines`` / ``terminal_columns`` — best-effort viewport size.
* ``decode_key`` / ``read_key_raw``          — keypress reading + escape-sequence
                                               normalisation to semantic names.
* ``raw_screen``                             — async context manager giving a
                                               cbreak stdin (Ctrl+C delivered as a
                                               byte) + optional alternate screen,
                                               yielding an ``await get_key()``.
* ``ScrollView``                             — pure scroll/viewport bookkeeping with
                                               follow-tail semantics.
* ``wrap_text_lines`` / ``compose_frame``     — slice a Rich ``Text`` into a scrollable
                                               window of screen lines.
* ``page_output``                            — hand a (possibly ANSI) string to the
                                               user's pager.

Nothing here talks to the CAN bus; it is intentionally dependency-light so any
mode can import it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import shutil
import subprocess
import sys
import termios
from collections.abc import Callable

from rich.console import Console
from rich.segment import Segment, Segments
from rich.text import Text

__all__ = [
    "ScrollView",
    "compose_frame",
    "decode_key",
    "page_output",
    "raw_screen",
    "read_key_raw",
    "segments_to_str",
    "terminal_columns",
    "terminal_lines",
    "wrap_text_lines",
]


# ---------------------------------------------------------------------------
# Terminal size
# ---------------------------------------------------------------------------


def terminal_lines(default: int = 24) -> int:
    """Best-effort terminal height for viewport sizing."""
    return shutil.get_terminal_size(fallback=(120, default)).lines


def terminal_columns(default: int = 120) -> int:
    """Best-effort terminal width for layout."""
    return shutil.get_terminal_size(fallback=(default, 24)).columns


# ---------------------------------------------------------------------------
# Keyboard input
# ---------------------------------------------------------------------------

# Raw escape sequence -> semantic key name. Covers the common xterm/VT variants
# emitted for arrows, paging and home/end across terminals (``\x1b[`` CSI and
# ``\x1bO`` SS3 forms, plus the numeric ``~`` variants).
_KEY_MAP = {
    "\x1b[A": "UP",
    "\x1bOA": "UP",
    "\x1b[B": "DOWN",
    "\x1bOB": "DOWN",
    "\x1b[C": "RIGHT",
    "\x1bOC": "RIGHT",
    "\x1b[D": "LEFT",
    "\x1bOD": "LEFT",
    "\x1b[5~": "PGUP",
    "\x1b[6~": "PGDN",
    "\x1b[H": "HOME",
    "\x1bOH": "HOME",
    "\x1b[1~": "HOME",
    "\x1b[7~": "HOME",
    "\x1b[F": "END",
    "\x1bOF": "END",
    "\x1b[4~": "END",
    "\x1b[8~": "END",
    "\r": "ENTER",
    "\n": "ENTER",
    "\x7f": "BACKSPACE",
    "\x08": "BACKSPACE",
    "\t": "TAB",
    "\x03": "CTRL_C",
    "\x1b": "ESC",
}


def decode_key(raw: str) -> str:
    """Map a raw keypress string to a semantic name, or return it unchanged.

    Recognised control/navigation keys become names like ``UP``/``PGDN``/
    ``ENTER``/``CTRL_C``; printable characters (``q``, ``j``, ``G`` …) and
    unrecognised sequences are returned verbatim so callers can match on them.
    """
    return _KEY_MAP.get(raw, raw)


def read_key_raw(fd: int) -> str:
    """Blocking read of a single keypress / escape sequence from a cbreak fd."""
    return os.read(fd, 16).decode("utf-8", errors="ignore")


@contextlib.asynccontextmanager
async def raw_screen(
    *,
    alt_screen: bool = True,
    hide_cursor: bool = True,
    disable_signals: bool = True,
):
    """Async context manager: cbreak stdin (+ optional alternate screen).

    Puts the terminal into cbreak mode and registers an asyncio reader that
    feeds keypresses into a queue. Yields an awaitable ``get_key(timeout=None)``
    that returns the next *decoded* key (see :func:`decode_key`) or ``None`` on
    timeout. On exit the terminal is always restored (reader removed, termios
    reset, cursor shown, alternate screen left).

    ``disable_signals`` additionally clears ``ISIG`` so Ctrl+C arrives as a
    ``CTRL_C`` key rather than raising ``KeyboardInterrupt`` — giving callers a
    single, deterministic quit path.

    When stdin is not a TTY the terminal is left untouched and ``get_key``
    always returns ``None`` (callers should provide their own non-interactive
    loop).
    """
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()
    old_settings = None
    try:
        fd = sys.stdin.fileno()
        is_tty = os.isatty(fd)
    except (OSError, ValueError, AttributeError):
        fd, is_tty = -1, False

    def _on_stdin_ready():
        try:
            data = os.read(fd, 16).decode("utf-8", errors="ignore")
            if data:
                queue.put_nowait(data)
        except Exception:
            pass

    if is_tty:
        old_settings = termios.tcgetattr(fd)
        mode = termios.tcgetattr(fd)
        mode[3] &= ~(termios.ECHO | termios.ICANON)  # LFLAG
        if disable_signals:
            mode[3] &= ~termios.ISIG
        mode[6][termios.VMIN] = 1  # CC
        mode[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSAFLUSH, mode)
        loop.add_reader(fd, _on_stdin_ready)
        if alt_screen:
            sys.stdout.write("\033[?1049h")
        if hide_cursor:
            sys.stdout.write("\033[?25l")
        if alt_screen or hide_cursor:
            sys.stdout.flush()

    async def get_key(timeout: float | None = None) -> str | None:
        if not is_tty:
            if timeout:
                await asyncio.sleep(timeout)
            return None
        try:
            raw = await asyncio.wait_for(queue.get(), timeout=timeout)
        except TimeoutError:
            return None
        return decode_key(raw)

    try:
        yield get_key
    finally:
        if is_tty:
            with contextlib.suppress(Exception):
                loop.remove_reader(fd)
            with contextlib.suppress(Exception):
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            if hide_cursor:
                sys.stdout.write("\033[?25h")
            if alt_screen:
                sys.stdout.write("\033[?1049l")
            if alt_screen or hide_cursor:
                sys.stdout.flush()


# ---------------------------------------------------------------------------
# Scroll viewport
# ---------------------------------------------------------------------------


class ScrollView:
    """Scroll/viewport bookkeeping for a fixed-height window over N lines.

    Tracks the top visible line and a *follow-tail* flag. While following, the
    window sticks to the bottom as content grows (like ``tail -f``); any upward
    scroll detaches follow, and scrolling/jumping back to the bottom re-attaches
    it. Purely arithmetic — no I/O — so it is trivially unit-testable.
    """

    def __init__(self, follow: bool = True):
        self.top = 0
        self.follow = follow
        self.total = 0
        self.viewport = 1

    @property
    def max_top(self) -> int:
        return max(0, self.total - self.viewport)

    @property
    def bottom(self) -> int:
        """One-past-the-last visible line index."""
        return min(self.total, self.top + self.viewport)

    def update(self, total: int, viewport: int) -> None:
        """Record current content/viewport size and re-clamp the offset."""
        self.total = max(0, total)
        self.viewport = max(1, viewport)
        if self.follow:
            self.top = self.max_top
        else:
            self.top = max(0, min(self.top, self.max_top))

    def scroll(self, delta: int) -> None:
        """Move the window by ``delta`` lines (negative = up)."""
        self.top = max(0, min(self.top + delta, self.max_top))
        # Detach follow when moving up; re-attach once pinned to the bottom.
        self.follow = self.top >= self.max_top

    def page(self, pages: int) -> None:
        """Move by whole pages, keeping one line of overlap."""
        self.scroll(pages * max(1, self.viewport - 1))

    def home(self) -> None:
        self.follow = False
        self.top = 0

    def end(self) -> None:
        self.follow = True
        self.top = self.max_top

    def toggle_follow(self) -> None:
        self.follow = not self.follow
        if self.follow:
            self.top = self.max_top


# ---------------------------------------------------------------------------
# Rich Text -> scrollable frame
# ---------------------------------------------------------------------------


def wrap_text_lines(text: Text, width: int, console: Console) -> list[list[Segment]]:
    """Render a Rich ``Text`` into per-screen-line segment lists at ``width``.

    Accounts for soft-wrapping, so the returned line count matches the number of
    rows the content actually occupies (what the scroll maths needs).
    """
    options = console.options.update(width=max(1, width), height=None)
    return console.render_lines(text, options, pad=False)


def segments_to_str(console: Console, lines: list[list[Segment]]) -> str:
    """Serialise a list of segment-lines back to an (ANSI) string."""
    flat: list[Segment] = []
    for line in lines:
        flat.extend(line)
        flat.append(Segment.line())
    with console.capture() as cap:
        console.print(Segments(flat), end="")
    return cap.get()


def compose_frame(
    console: Console,
    body: Text,
    scroll: ScrollView,
    *,
    width: int,
    height: int,
    footer: Text | Callable[[ScrollView], Text] | None = None,
    footer_rows: int = 1,
) -> str:
    """Build a full alternate-screen frame (clear + visible window + footer).

    Wraps ``body`` to ``width``, updates ``scroll`` for the available viewport
    (``height`` minus the footer and a one-line cushion so the bottom row never
    scrolls), and returns an ANSI string ready to ``sys.stdout.write``.

    ``footer`` may be a ``Text`` or a callable ``(ScrollView) -> Text``. The
    callable form is evaluated *after* the scroll offset has been updated, so a
    status line can report the current ``top``/``bottom``/``total`` for this
    exact frame. Footers taller than ``footer_rows`` are cropped.
    """
    lines = wrap_text_lines(body, width, console)
    viewport = max(1, height - footer_rows - 1)
    scroll.update(total=len(lines), viewport=viewport)
    window = lines[scroll.top : scroll.top + viewport]
    content = segments_to_str(console, window).rstrip("\n")

    parts = ["\033[2J\033[H", content]
    footer_text = footer(scroll) if callable(footer) else footer
    if footer_text is not None:
        footer_lines = wrap_text_lines(footer_text, width, console)[:footer_rows]
        parts.append("\n")
        parts.append(segments_to_str(console, footer_lines).rstrip("\n"))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


def page_output(content: str) -> None:
    """Display ``content`` through the user's pager (``$PAGER`` or ``less -R``).

    ANSI colours are preserved (``less -R``). Falls back to a plain ``print``
    when stdout is not a TTY or the pager cannot be launched.
    """
    if not content:
        return
    if not sys.stdout.isatty():
        print(content)
        return
    pager = os.environ.get("PAGER") or "less -R"
    try:
        proc = subprocess.Popen(shlex.split(pager), stdin=subprocess.PIPE)
        proc.communicate(content.encode("utf-8", errors="replace"))
    except Exception:
        print(content)
