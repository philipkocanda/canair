"""The write gate shared by the session-metadata editors (``--set-state``/``--set-notes``).

Both rewrite one field on every scope-selected session, so both must refuse a
read-only base layer and confirm before writing — on a TTY by prompt, otherwise
only with ``--yes``. Stated once here so the two cannot drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def refuse_write(
    paths: Iterable[Path], *, verb: str, prompt: str, what: str, assume_yes: bool
) -> int | None:
    """Return an exit code when the write must not happen, else None to proceed.

    ``verb`` names the change in the layer refusal (``"relabelled"``), ``prompt``
    is the confirmation question, and ``what`` completes the non-interactive
    refusal (``"set state"``).
    """
    from . import layers

    if blocked := layers.refusal(paths, verb):
        print(blocked, file=sys.stderr)
        return 1

    if assume_yes:
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            f"  error: refusing to {what} without confirmation "
            "(pass --yes for non-interactive use, or --dry-run to preview).",
            file=sys.stderr,
        )
        return 2
    if input(f"  {prompt} [y/N] ").strip().lower() not in ("y", "yes"):
        print("  Cancelled — nothing written.")
        return 1
    return None
