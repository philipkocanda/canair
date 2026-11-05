"""The write gate shared by the session-metadata editors (the ``--set-*`` modes).

Each rewrites one field on every scope-selected session, so each must refuse a
read-only base layer and confirm before writing — on a TTY by prompt, otherwise
only with ``--yes``. Stated once here so the editors cannot drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from canair.captures import layers
from canair.core.prompt import confirm

if TYPE_CHECKING:
    from collections.abc import Iterable


def refuse_write(paths: Iterable[Path], *, verb: str, prompt: str, assume_yes: bool) -> int | None:
    """Return an exit code when the write must not happen, else None to proceed.

    ``verb`` names the change in the layer refusal (``"relabelled"``) and ``prompt`` is the
    confirmation question. A decline returns 1 and no terminal without ``--yes`` raises
    ``PromptUnavailable`` (exit 2). The question goes to stderr, so stdout stays the result.
    """
    if blocked := layers.refusal(paths, verb):
        print(blocked, file=sys.stderr)
        return 1
    return None if confirm(prompt, yes=assume_yes) else 1
