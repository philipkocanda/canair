"""The confirmation line every profile-authoring command prints after a write.

`pids`, `signals`, `states`, `groups` and `ecu` all end the same way: say what
changed, and say where it landed. Centralising that one line buys two things a
per-command f-string kept getting wrong:

* **The full path, never a bare filename.** ``(bms.yaml)`` does not identify a
  profile, and canair ships several — plus a user copy that shadows a bundled one
  by name. :func:`canlib.captures.saved_banner` has always held this rule for
  capture writes; this is its authoring twin.
* **A write into an install snapshot has to say so.** Editing definitions under
  ``site-packages`` appears to work and is erased by the next reinstall, so the
  warning belongs where the data landed rather than only at contribution time
  (see :func:`canlib.install_context.snapshot_write_note`).
"""

from __future__ import annotations

from pathlib import Path

from .. import ansi
from ..install_context import snapshot_write_note


def edit_line(what: str, path: Path) -> str:
    """Format the confirmation without printing it (for tests and composition)."""
    return f"{ansi.GREEN}  ✓ {what}{ansi.RESET}  {ansi.DIM}({path}){ansi.RESET}"


def echo_edit(what: str, path: Path) -> None:
    """Confirm an edit to ``path``, warning when it landed in an install snapshot."""
    print(edit_line(what, path))
    note = snapshot_write_note(path)
    if note:
        print(note)
