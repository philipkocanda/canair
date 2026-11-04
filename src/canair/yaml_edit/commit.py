"""The one write path: write a file, re-check it, and put the old text back on any failure.

Every editor in :mod:`canair.yaml_edit` ends here. A change reaches disk only if the file
re-parses and passes its checks; otherwise the original text (or, for a file that did not exist,
no file) is restored, so a rejected edit never persists. Writes go through a temp file and
``os.replace``, so a crash mid-write leaves the old file whole. A file's line endings are kept.
"""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .errors import EditError


def noop() -> None:
    """The default ``after_write``: nothing to refresh."""


_schema_override: ContextVar[bool | None] = ContextVar("yaml_edit_schema", default=None)


@contextmanager
def schema_validation(enabled: bool) -> Iterator[None]:
    """Force the heavy schema check on (or off) for every edit made inside the block.

    The commands wrap their edit in this so ``--no-validate`` reaches whichever engine writes
    the file, without a ``validate`` argument on every editor function.
    """
    token = _schema_override.set(enabled)
    try:
        yield
    finally:
        _schema_override.reset(token)


def resolve_validate(default: bool) -> bool:
    """Whether to run the schema check: the :func:`schema_validation` override, else ``default``."""
    override = _schema_override.get()
    return default if override is None else override


def file_newline(path: Path) -> str:
    r"""The line ending ``path`` uses on disk (``"\r\n"`` or ``"\n"``; ``"\n"`` if unreadable).

    Every editor reads with universal newlines, so its text is always LF; this is how a CRLF
    file keeps its line endings when written back.
    """
    try:
        with path.open("rb") as f:
            head = f.read(65536)
    except OSError:
        return "\n"
    nl = head.find(b"\n")
    return "\r\n" if nl > 0 and head[nl - 1 : nl] == b"\r" else "\n"


def _default_mode() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def write_atomic(path: Path, text: str, newline: str = "\n") -> None:
    """Replace ``path`` with ``text`` (LF text, written with ``newline``) via a temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else _default_mode()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def restore(path: Path, original: str | None, newline: str = "\n") -> None:
    """Put ``original`` back, or delete the file when there was none."""
    if original is None:
        path.unlink(missing_ok=True)
    else:
        write_atomic(path, original, newline)


def commit_text(
    path: Path,
    new_text: str,
    original: str | None,
    *,
    check: Callable[[Path], None] | None,
    error: type[EditError],
    validate: Callable[[Path], None] | None = None,
    after_write: Callable[[], None] = noop,
) -> None:
    """Write ``new_text`` to ``path``, run the checks, and revert on any failure.

    ``original`` is the file's text before the edit (``None`` if it did not exist). ``check``
    is the cheap structural check of the edit itself; ``validate`` is the heavier schema check
    a caller may switch off (``--no-validate``). Both raise ``error`` to reject the file; any
    other exception becomes ``error("edit failed post-check, reverted: …")``. ``after_write``
    runs after the write and again after a revert (it drops stale caches).
    """
    newline = file_newline(path) if original is not None else "\n"
    write_atomic(path, new_text, newline)
    after_write()
    try:
        if check is not None:
            check(path)
        if validate is not None:
            validate(path)
    except EditError:
        restore(path, original, newline)
        after_write()
        raise
    except Exception as e:
        restore(path, original, newline)
        after_write()
        raise error(f"edit failed post-check, reverted: {e}") from e
