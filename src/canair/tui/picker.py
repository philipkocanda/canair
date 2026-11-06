"""The arrow-key list picker of ``canair profile``: a Rich screen over single-key reads."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence

from canair.render import ansi
from canair.render.console import console
from canair.render.glyphs import CURSOR
from canair.tui import styles
from canair.tui.keys import CANCEL_HINT, arrows_hint, hint, key_hint

#: The footer of the picker. ``esc`` cancels it, like any dialog: it returns a value and
#: exits, so it is not a screen to back out of.
PICKER_FOOTER = hint(arrows_hint("select"), "accept", key_hint("quit"), CANCEL_HINT)


def read_key_raw(fd: int) -> str:
    """Blocking read of a single keypress / escape sequence from a cbreak fd."""
    return os.read(fd, 16).decode("utf-8", errors="ignore")


def key_to_action(key: str) -> str:
    """Map a raw keypress to a list-navigation action.

    Returns one of ``"select"``, ``"cancel"``, ``"up"``, ``"down"``, ``"home"``,
    ``"end"``, or ``""`` (ignore). Pure and I/O-free so it can be unit-tested;
    the ordering matters — double-Esc must beat a lone Esc, and arrow escape
    sequences arrive as a single multi-byte read from a cbreak fd.
    """
    if key in ("\r", "\n"):
        return "select"
    if key in ("q", "\x1b\x1b", "\x03", "\x1b"):  # q, double-esc, Ctrl-C, bare Esc
        return "cancel"
    if key in ("\x1b[A", "\x1bOA", "k"):
        return "up"
    if key in ("\x1b[B", "\x1bOB", "j"):
        return "down"
    if key in ("\x1b[H", "\x1b[1~", "\x1bOH", "g"):
        return "home"
    if key in ("\x1b[F", "\x1b[4~", "\x1bOF", "G"):
        return "end"
    return ""


def select_from_list[T](
    items: Sequence[T],
    *,
    title: str,
    render: Callable[[T], str],
    initial: int = 0,
    footer: str = PICKER_FOOTER,
) -> T | None:
    """Interactive arrow-key single-select over ``items``.

    Renders a Rich list on the alternate screen, letting the user move the
    cursor with ↑/↓ (or k/j), confirm with Enter, and cancel with q/Esc/Ctrl-C.
    Returns the chosen item, or ``None`` if cancelled.

    ``render`` maps an item to its (Rich-markup) display line. The caller is
    responsible for only calling this on an interactive TTY — it reads raw keys
    from ``stdin`` and expects ``stdout`` to be a terminal.
    """
    import termios
    import tty

    from rich.console import Group
    from rich.live import Live
    from rich.text import Text

    if not items:
        return None

    out = console()
    cursor = max(0, min(initial, len(items) - 1))

    def _frame() -> Group:
        rows: list[Text] = [Text(title, style=styles.HEADING_NAME), Text()]
        for i, item in enumerate(items):
            selected = i == cursor
            # Parse the caller's Rich markup, then prefix a cursor marker and,
            # when selected, overlay a highlight across the whole row.
            line = Text.from_markup(render(item))
            marker = Text(
                f"{CURSOR} " if selected else "  ",
                style=styles.HEADING_NAME if selected else "",
            )
            row = marker + line
            if selected:
                row.stylize(styles.HEADING)
            rows.append(row)
        rows.append(Text())
        rows.append(Text(footer, style=styles.MUTED))
        return Group(*rows)

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    sys.stdout.write(ansi.screen.ALT_SCREEN_ON + ansi.screen.HIDE_CURSOR)
    sys.stdout.flush()
    try:
        tty.setcbreak(fd)
        with Live(_frame(), console=out, auto_refresh=False, screen=False) as live:
            while True:
                action = key_to_action(read_key_raw(fd))
                if action == "select":
                    return items[cursor]
                if action == "cancel":
                    return None
                if action == "up":
                    cursor = (cursor - 1) % len(items)
                elif action == "down":
                    cursor = (cursor + 1) % len(items)
                elif action == "home":
                    cursor = 0
                elif action == "end":
                    cursor = len(items) - 1
                else:
                    continue
                live.update(_frame(), refresh=True)
    except KeyboardInterrupt:
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write(ansi.screen.SHOW_CURSOR + ansi.screen.ALT_SCREEN_OFF)
        sys.stdout.flush()
