"""Rewrite capture files in the canonical folded form (``canair captures compact``).

Every write already stores captures folded and one per line
(:func:`canlib.captures.io.capture_text`), so a file reaches that form the next
time anything rewrites it. This converts the rest in one pass. The rewrite is
lossless by construction — the loaded document is dumped unchanged — and each
file is verified by re-parsing the new text before it replaces the old one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from canlib.captures import io as capture_io


class CompactError(Exception):
    """A rewrite would not reload as the same document (nothing was written)."""


@dataclass(frozen=True)
class CompactResult:
    """Outcome of compacting one capture file."""

    path: Path
    before: int  # bytes on disk before
    after: int  # bytes of the canonical text
    written: bool  # False in --dry-run, or when the file was already canonical

    @property
    def changed(self) -> bool:
        return self.before != self.after


def compact_file(path: Path, *, dry_run: bool = False) -> CompactResult:
    """Rewrite one capture file in the canonical form (idempotent)."""
    old = path.read_text()
    data = capture_io.parse_capture_text(old)
    new = capture_io.capture_text(data)
    if new == old:
        return CompactResult(path, len(old.encode()), len(new.encode()), written=False)
    if capture_io.parse_capture_text(new) != data:
        raise CompactError(f"{path.name}: the compacted text does not reload identically")
    if not dry_run:
        capture_io.dump_capture_file(path, data)
    return CompactResult(path, len(old.encode()), len(new.encode()), written=not dry_run)


def compact_paths(paths: list[Path], *, dry_run: bool = False) -> list[CompactResult]:
    """Compact every file in ``paths``."""
    return [compact_file(p, dry_run=dry_run) for p in paths]
