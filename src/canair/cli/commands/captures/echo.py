"""The confirmation every captures editor prints after a write: ``✓ what  (full path)``.

The same shape as :func:`canair.yaml_edit.echo.echo_edit`, but it writes to the stream the
editor reports on, which is stderr under ``--json``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from canair.install.context import snapshot_write_note
from canair.render import ansi, glyphs
from canair.render.paint import Painter


def _where(paths: Iterable[Path]) -> Path | None:
    """The one file written, else the directory that holds them all."""
    distinct = sorted({Path(p) for p in paths})
    if not distinct:
        return None
    if len(distinct) == 1:
        return distinct[0]
    return Path(os.path.commonpath([p.parent for p in distinct]))


def echo_write(what: str, paths: Iterable[Path], out: TextIO | None = None) -> None:
    """Print ``✓ what`` in green with the full path of what was written, dimmed."""
    out = out or sys.stdout
    paint = Painter(out)
    where = _where(paths)
    line = paint(f"  {glyphs.OK} {what}", ansi.raw.GREEN)
    if where is not None:
        line += f"  {paint(f'({where})', ansi.raw.DIM)}"
    print(line, file=out)
    if where is not None and (note := snapshot_write_note(where)):
        print(note, file=out)
