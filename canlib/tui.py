"""Small terminal helpers shared across modes.

Kept intentionally dependency-light so any mode can import it. The live monitor
now uses Textual (:mod:`canlib.modes._monitor_tui`); the remaining consumers here
are the IOControl / routines TUIs (viewport sizing) and the capture stepper
(single-key reads).
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Sequence

__all__ = [
    "key_to_action",
    "read_key_raw",
    "select_from_list",
    "terminal_columns",
    "terminal_lines",
]


def terminal_lines(default: int = 24) -> int:
    """Best-effort terminal height for viewport sizing."""
    return shutil.get_terminal_size(fallback=(120, default)).lines


def terminal_columns(default: int = 120) -> int:
    """Best-effort terminal width for layout."""
    return shutil.get_terminal_size(fallback=(default, 24)).columns


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
    if key in ("\x1b[H", "g"):
        return "home"
    if key in ("\x1b[F", "G"):
        return "end"
    return ""


def select_from_list[T](
    items: Sequence[T],
    *,
    title: str,
    render: Callable[[T], str],
    initial: int = 0,
    footer: str = "↑/↓ move · enter select · q/esc cancel",
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

    from rich.console import Console, Group
    from rich.live import Live
    from rich.text import Text

    if not items:
        return None

    console = Console()
    cursor = max(0, min(initial, len(items) - 1))

    def _frame() -> Group:
        rows: list[Text] = [Text(title, style="bold cyan"), Text()]
        for i, item in enumerate(items):
            selected = i == cursor
            # Parse the caller's Rich markup, then prefix a cursor marker and,
            # when selected, overlay a highlight across the whole row.
            line = Text.from_markup(render(item))
            marker = Text("▶ " if selected else "  ", style="bold cyan" if selected else "")
            row = marker + line
            if selected:
                row.stylize("bold")
            rows.append(row)
        rows.append(Text())
        rows.append(Text(footer, style="dim"))
        return Group(*rows)

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    sys.stdout.write("\033[?1049h\033[?25l")  # alt screen + hide cursor
    sys.stdout.flush()
    try:
        tty.setcbreak(fd)
        with Live(_frame(), console=console, auto_refresh=False, screen=False) as live:
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
        sys.stdout.write("\033[?25h\033[?1049l")  # show cursor, leave alt screen
        sys.stdout.flush()
