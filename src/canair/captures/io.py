"""Capture-file format seam (JSON).

The diagnostic capture store (``captures/YYYY-MM-DD.json``) is machine-written
and read on every history-consuming command, so it is stored as JSON — the
parse is ~60x faster than the equivalent YAML (the dominant cost of ``ecu``,
``coverage``, ``decode``, ``correlate``, ``hunt``, ``investigate``, ``captures``,
``validate captures``). This module is the single place that knows the on-disk
format: readers/writers go through it rather than opening files directly, so the
format lives in one seam (mirroring :mod:`canair.core.yaml_io`).

Capture files are **never hand-written**; they are produced by the
``canair.captures`` helpers (the ``--save`` / journal-reconcile path) and edited
via those helpers.

On disk, repeated polls are folded into their run's first row
(:mod:`canair.captures.folding`) and each capture, like each ``state_spans`` span, is one
line; in memory every file is expanded back to one row per poll.
:func:`parse_capture_text` and :func:`capture_text` are the two directions, so a reader
that bypasses them (a bare ``json.loads``) sees the folded form.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import IO, Any

from canair.captures.folding import CaptureFormatError, expand_file, fold_file
from canair.captures.types import CaptureFile
from canair.profile.model import Profile

# On-disk capture-file extension. Legacy profiles used ``.yaml`` (pre-migration);
# see :func:`find_legacy_yaml` and ``canair captures migrate``.
CAPTURE_SUFFIX = ".json"
LEGACY_SUFFIX = ".yaml"

# Non-capture files that share the captures/ directory and must be skipped when
# globbing: the human SCHEMA doc and any underscore-prefixed helper file.
_SKIP_PREFIXES = ("SCHEMA", "_")


class LegacyCaptureError(Exception):
    """Raised when a profile still has legacy YAML capture files.

    Capture data is JSON-only; there is no dual-format read path. The supported
    fix is ``canair captures migrate``.
    """


def _is_capture_file(path: Path) -> bool:
    return not path.name.startswith(_SKIP_PREFIXES)


def resolve_captures_dir(explicit: Path | None = None, *, profile: Profile) -> Path:
    """The captures directory to work in: ``explicit`` (``--dir``), else the profile's.

    Every command that reads or writes the store needs this, so it lives here
    rather than being re-derived per caller.
    """
    return explicit if explicit is not None else profile.captures_dir


def resolve_capture_layers(explicit: Path | None = None, *, profile: Profile) -> list[Path]:
    """Every captures directory to *read*, least specific (the base) first.

    An explicit ``--dir`` means exactly that directory. Otherwise a layered
    profile contributes its read-only base's store followed by the user's
    overlay, so analysis sees the whole history while
    :func:`resolve_captures_dir` keeps writes in the overlay.
    """
    return [explicit] if explicit is not None else profile.capture_layers


def iter_capture_files(captures_dir: Path) -> list[Path]:
    """Sorted list of capture JSON files in ``captures_dir`` (SCHEMA/_ skipped)."""
    return [p for p in sorted(captures_dir.glob(f"*{CAPTURE_SUFFIX}")) if _is_capture_file(p)]


def find_legacy_yaml(captures_dir: Path) -> list[Path]:
    """Sorted list of legacy ``*.yaml`` capture files (SCHEMA/_ skipped).

    Used to fail fast when a not-yet-migrated profile is loaded: capture data is
    JSON-only, and the supported migration path is ``canair captures migrate``.
    """
    return [p for p in sorted(captures_dir.glob(f"*{LEGACY_SUFFIX}")) if _is_capture_file(p)]


def ensure_migrated(captures_dir: Path) -> None:
    """Raise :class:`LegacyCaptureError` if ``captures_dir`` holds legacy YAML.

    Called by the capture readers so a pre-migration profile fails fast with an
    actionable message instead of silently reading nothing (JSON is the only
    on-disk format). No-op once the profile is migrated.
    """
    legacy = find_legacy_yaml(captures_dir)
    if legacy:
        names = ", ".join(p.name for p in legacy[:3]) + (" …" if len(legacy) > 3 else "")
        raise LegacyCaptureError(
            f"{len(legacy)} legacy YAML capture file(s) in {captures_dir} ({names}). "
            "Capture data is stored as JSON now — run `canair captures migrate` to convert."
        )


def parse_capture_text(text: str) -> CaptureFile:
    """Parse capture-file text, expanding folded repeats to one row per poll.

    The in-memory form of every capture file. Raises ``json.JSONDecodeError`` for
    text that is not JSON and :class:`~canair.captures.folding.CaptureFormatError`
    for a malformed repeat encoding — both ``ValueError``.
    """
    return expand_file(json.loads(text))


def load_capture_file(path: Path) -> CaptureFile:
    """Parse one capture file (see :func:`parse_capture_text`).

    Inside :func:`reading_once`, a file is parsed once and the same document is
    returned to every later caller.
    """
    memo = _READ_ONCE.get()
    if memo is not None:
        key = path.resolve()
        if key not in memo:
            memo[key] = _load_capture_file(path)
        return memo[key]
    return _load_capture_file(path)


def _load_capture_file(path: Path) -> CaptureFile:
    """Read and parse one capture file; every failure to do so names the file.

    Raises :class:`~canair.captures.folding.CaptureFormatError` for text that is not UTF-8
    JSON (an empty or truncated file, a byte-order mark) as well as for a malformed repeat
    encoding, so a caller can tell the user which file to repair.
    """
    try:
        return parse_capture_text(path.read_text(encoding="utf-8"))
    except CaptureFormatError as e:
        raise CaptureFormatError(f"{path.name}: {e}") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise CaptureFormatError(f"{path.name}: not a readable capture file ({e})") from e


_READ_ONCE: ContextVar[dict[Path, CaptureFile] | None] = ContextVar("_READ_ONCE", default=None)


@contextmanager
def reading_once() -> Generator[None]:
    """Parse each capture file at most once while the block runs.

    For read-only passes that run several checks over the same files (``validate
    captures`` read each file eleven times). Every caller gets the *same* document,
    so nothing inside the block may modify one or write a capture file.
    """
    token = _READ_ONCE.set({})
    try:
        yield
    finally:
        _READ_ONCE.reset(token)


def load_sessions(path: Path) -> list[tuple[int, Mapping[str, Any]]]:
    """``(index, session)`` for each session in a capture file, which must be well formed.

    A file that parses as JSON can still hold the wrong shapes (``null``, a ``null`` session).
    That raises an error naming the file, never a bare ``TypeError`` from deep in a reader.
    """
    data = load_capture_file(path)
    if not isinstance(data, Mapping):
        raise CaptureFormatError(f"{path.name}: the top level must be an object")
    sessions = data.get("sessions", [])
    if not isinstance(sessions, list):
        raise CaptureFormatError(f"{path.name}: sessions must be a list")
    for index, session in enumerate(sessions):
        if not isinstance(session, Mapping):
            raise CaptureFormatError(f"{path.name}: sessions[{index}] must be an object")
    return list(enumerate(sessions))


def session_captures(session: Mapping[str, Any], path: Path, index: int) -> list[Mapping[str, Any]]:
    """A session's captures, which must be a list of objects (``null`` is an empty list)."""
    captures = session.get("captures") or []
    if not isinstance(captures, list) or not all(isinstance(c, Mapping) for c in captures):
        raise CaptureFormatError(
            f"{path.name}: sessions[{index}].captures must be a list of objects"
        )
    return captures


def capture_rx(cap: Mapping[str, Any]) -> str:
    """Read a capture's RX (CAN response) address, tolerating the legacy key.

    The field was renamed ``ecu`` → ``rx`` (it holds the RX/response address,
    not an ECU name; see
    the `rx` rename). Capture files
    are migrated in place (``canair captures migrate-rx``), but this fallback
    keeps an un-migrated file or a stale journal readable — the store is
    append-only public data, so a read must never depend on migration having
    run. Returns ``""`` when neither key is present.
    """
    return str(cap.get("rx") or cap.get("ecu") or "")


def capture_text(data: Any) -> str:
    """The canonical on-disk JSON text of a capture file (see :func:`dump_capture_file`).

    Repeated polls are folded (:mod:`canair.captures.folding`), and the document is
    pretty-printed with ``indent=2`` except that each capture, each ``state_spans``
    span and each link event is one line: a session's metadata stays readable while a
    long recording costs a line per stored capture, not one per field.
    """
    return _render(fold_file(data), 0, ()) + "\n"


# Lists whose items each sit on one line, keyed by the dict-key path from the document root
# (list indices are not part of the path), with the separators used inside that line.
_ONE_LINE_ITEMS: dict[tuple[str, ...], tuple[str, str]] = {
    ("sessions", "captures"): (",", ":"),
    ("sessions", "state_spans", "spans"): (", ", ": "),
    ("sessions", "link_events"): (", ", ": "),
    ("sessions", "actuations"): (", ", ": "),
}


def _key(key: Any) -> str:
    """A dict key as ``json.dumps`` writes it: a non-string key becomes a quoted string."""
    if isinstance(key, str):
        return json.dumps(key, ensure_ascii=False)
    return json.dumps({key: 0}, ensure_ascii=False)[1:-4]


def _render(value: Any, level: int, path: tuple[str, ...]) -> str:
    """``json.dumps(value, indent=2)``, with the :data:`_ONE_LINE_ITEMS` lists one item per line.

    Byte-identical to ``json.dumps(indent=2, ensure_ascii=False)`` for everything
    outside those lists.
    """
    pad, close = "  " * (level + 1), "  " * level
    if isinstance(value, dict) and value:
        items = (f"{pad}{_key(k)}: " + _render(v, level + 1, (*path, k)) for k, v in value.items())
        return "{\n" + ",\n".join(items) + f"\n{close}}}"
    if isinstance(value, list) and value:
        separators = _ONE_LINE_ITEMS.get(path)
        if separators:
            rows = (pad + json.dumps(v, ensure_ascii=False, separators=separators) for v in value)
        else:
            rows = (pad + _render(v, level + 1, path) for v in value)
        return "[\n" + ",\n".join(rows) + f"\n{close}]"
    return json.dumps(value, ensure_ascii=False)


def dump_capture_file(path: Path, data: Any) -> None:
    """Write ``data`` to ``path`` in the canonical text form, atomically.

    The text (:func:`capture_text`) stays git-diffable and readable for review
    (the tree is public); insertion order is preserved (no ``sort_keys``) so the
    builder's field grouping and diffs stay stable. Written via a temp file +
    ``os.replace`` so a crash never leaves a half-written file.
    """
    text = capture_text(data)
    json.loads(text)  # never replace a readable file with text that does not parse
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = os.stat(path).st_mode & 0o7777 if path.exists() else None
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# -- journal files and write locking -------------------------------------------------

# Both locks use ``flock(2)``, which the kernel drops when the holding process dies, so neither
# can go stale and neither needs a pid check.
#
# * A **journal lock** is held on a journal's own file for as long as its writer lives. A journal
#   nobody holds is an orphan to recover. One somebody holds belongs to a running session, and
#   recovering it would delete the file under the writer and split the session in two.
# * The **write lock** serialises read-modify-write of the capture files in one captures
#   directory. Two sessions that reconcile at once would otherwise both read the day's file, each
#   append their own session and write: the later write drops the earlier session.

JOURNAL_RECONCILED = "reconciled"


def read_records(path: Path) -> list[dict]:
    """The JSON objects of a journal, skipping what a kill left unreadable.

    A killed process can leave a truncated last line, cut in the middle of a multi-byte
    character too. That line is dropped, never the journal.
    """
    records: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records


def mark_reconciled(path: Path, date: str) -> None:
    """Record that the session for ``date`` is saved, so a retry does not save it twice.

    A journal that spans midnight saves one session per day. If the second save fails, the
    journal stays for recovery, and without this mark the recovery would save the first day
    again.
    """
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": JOURNAL_RECONCILED, "date": date}) + "\n")
        f.flush()
        os.fsync(f.fileno())


def reconciled_dates(records: list[dict]) -> set[str]:
    """The dates whose sessions an earlier, interrupted reconcile already saved."""
    return {str(r.get("date")) for r in records if r.get("type") == JOURNAL_RECONCILED}


WRITE_LOCK_NAME = "write.lock"


class _DirLock:
    """One captures directory's write lock: a thread lock in front of the ``flock``."""

    def __init__(self) -> None:
        self.guard = threading.RLock()
        self.depth = 0


_dir_locks: dict[Path, _DirLock] = {}
_registry_guard = threading.Lock()


def lock_journal(fh: IO) -> bool:
    """Take the journal lock on the open file ``fh``; False when another holder has it."""
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def journal_in_use(path: Path) -> bool:
    """Whether a live writer holds the journal at ``path``."""
    try:
        with open(path, "rb") as fh:
            if lock_journal(fh):
                return False
    except OSError:
        return False
    return True


@contextmanager
def write_lock(captures_dir: Path) -> Generator[None]:
    """Hold the write lock of ``captures_dir``; reentrant within a thread.

    The lock file lives in the journal directory, which git does not track.
    """
    lock_dir = captures_dir / ".journal"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with _registry_guard:
        lock = _dir_locks.setdefault(lock_dir.resolve(), _DirLock())
    with lock.guard:
        if lock.depth:
            lock.depth += 1
            try:
                yield
            finally:
                lock.depth -= 1
            return
        with open(lock_dir / WRITE_LOCK_NAME, "a") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            lock.depth = 1
            try:
                yield
            finally:
                lock.depth = 0
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
