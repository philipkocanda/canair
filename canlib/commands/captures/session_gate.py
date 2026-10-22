"""The write gate shared by the session-metadata editors (the ``--set-*`` modes).

Each rewrites one field on every scope-selected session, so each must refuse a
read-only base layer and confirm before writing — on a TTY by prompt, otherwise
only with ``--yes``. Stated once here so the editors cannot drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from collections.abc import Iterable


def refuse_write(
    paths: Iterable[Path],
    *,
    verb: str,
    prompt: str,
    what: str,
    assume_yes: bool,
    out: TextIO | None = None,
) -> int | None:
    """Return an exit code when the write must not happen, else None to proceed.

    ``verb`` names the change in the layer refusal (``"relabelled"``), ``prompt``
    is the confirmation question, and ``what`` completes the non-interactive
    refusal (``"set state"``). ``out`` receives the prompt and the cancellation
    note — stderr under ``--json``, so stdout carries nothing but the JSON.
    """
    from . import layers

    out = out or sys.stdout
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
    print(f"  {prompt} [y/N] ", end="", file=out, flush=True)
    if input().strip().lower() not in ("y", "yes"):
        print("  Cancelled — nothing written.", file=out)
        return 1
    return None
