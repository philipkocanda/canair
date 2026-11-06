"""The confirmation line every profile-authoring command prints after a write.

`pids`, `signals`, `states`, `groups` and `ecu` all end the same way: say what
changed, and say where it landed. Centralising that one line buys two things a
per-command f-string kept getting wrong:

* **The full path, never a bare filename.** ``(bms.yaml)`` does not identify a
  profile, and canair ships several — plus a user copy that shadows a bundled one
  by name. :func:`canair.captures.save.saved_banner` has always held this rule for
  capture writes; this is its authoring twin.
* **A write into an install snapshot has to say so.** Editing definitions under
  ``site-packages`` appears to work and is erased by the next reinstall, so the
  warning belongs where the data landed rather than only at contribution time
  (see :func:`canair.install.context.snapshot_write_note`).
"""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from canair.install.context import snapshot_write_note
from canair.render import ansi


def edit_line(what: str, path: Path) -> str:
    """Format the confirmation without printing it (for tests and composition)."""
    return f"{ansi.GREEN}  ✓ {what}{ansi.RESET}  {ansi.DIM}({path}){ansi.RESET}"


def echo_edit(what: str, path: Path, *, file: TextIO | None = None) -> None:
    """Confirm an edit to ``path``, warning when it landed in an install snapshot.

    ``file`` is the stream for a command whose stdout is its ``--json`` document; the colour
    then follows that stream.
    """
    if file is None:
        print(edit_line(what, path))
    else:
        done = ansi.c(f"  ✓ {what}", ansi.raw.GREEN, stream=file)
        print(f"{done}  {ansi.c(f'({path})', ansi.raw.DIM, stream=file)}", file=file)
    note = snapshot_write_note(path)
    if note:
        print(note, file=file)
