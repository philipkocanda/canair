"""The glyphs canair prints, one meaning each (the table in the cli-tui-standards skill).

Use these names instead of retyping the character, so a glyph cannot drift into a second
meaning or a second code point (``✗`` and ``✖``, ``…`` and ``...``).
"""

from __future__ import annotations

from typing import Final

OK: Final = "✓"  # success, verified
FAIL: Final = "✗"  # failure
WARN: Final = "⚠"  # a warning; print it in yellow
ARROW: Final = "→"  # "the result is at": a saved path, a rename target
CURSOR: Final = "▶"  # the selected row
SEP: Final = "·"  # separator between items on one line
EMPTY: Final = "—"  # an empty table cell, and a dash in a title
ELLIPSIS: Final = "…"  # truncation
BAR_FULL: Final = "█"  # the done part of a progress bar
BAR_EMPTY: Final = "░"  # the part of a progress bar still to do
DANGER: Final = "!!"  # the start of a prompt that may change the car or the device
