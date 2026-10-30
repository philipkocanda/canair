"""Capture-file format seam (JSON).

The diagnostic capture store (``captures/YYYY-MM-DD.json``) is machine-written
and read on every history-consuming command, so it is stored as JSON — the
parse is ~60x faster than the equivalent YAML (the dominant cost of ``ecu``,
``coverage``, ``decode``, ``correlate``, ``hunt``, ``investigate``, ``captures``,
``validate captures``). This module is the single place that knows the on-disk
format: readers/writers go through it rather than opening files directly, so the
format lives in one seam (mirroring :mod:`canlib.yaml_io`).

Capture files are **never hand-written**; they are produced by the
``canlib.captures`` helpers (the ``--save`` / journal-reconcile path) and edited
via those helpers.

On disk, repeated polls are folded into their run's first row
(:mod:`canlib.captures.folding`) and each capture, like each ``state_spans`` span, is one
line; in memory every file is expanded back to one row per poll.
:func:`parse_capture_text` and :func:`capture_text` are the two directions, so a reader
that bypasses them (a bare ``json.loads``) sees the folded form.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from canlib.captures.folding import CaptureFormatError, expand_file, fold_file
from canlib.captures.types import CaptureFile

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


def resolve_captures_dir(explicit: Path | None = None) -> Path:
    """The captures directory to work in: ``explicit`` (``--dir``), else the profile's.

    Every command that reads or writes the store needs this, so it lives here
    rather than being re-derived per caller (it previously existed as three
    verbatim copies under ``commands/captures/`` plus two open-coded sites). The
    profile import is deferred because ``canlib.profile`` pulls in the definition
    loaders, which this module must not depend on at import time.
    """
    if explicit is not None:
        return explicit
    from canlib.profile import active

    return active().captures_dir


def resolve_capture_layers(explicit: Path | None = None) -> list[Path]:
    """Every captures directory to *read*, least specific (the base) first.

    An explicit ``--dir`` means exactly that directory. Otherwise a layered
    profile contributes its read-only base's store followed by the user's
    overlay, so analysis sees the whole history while
    :func:`resolve_captures_dir` keeps writes in the overlay.
    """
    if explicit is not None:
        return [explicit]
    from canlib.profile import active

    return active().capture_layers


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
    text that is not JSON and :class:`~canlib.captures.folding.CaptureFormatError`
    for a malformed repeat encoding — both ``ValueError``.
    """
    return expand_file(json.loads(text))


def load_capture_file(path: Path) -> CaptureFile:
    """Parse one capture file (see :func:`parse_capture_text`)."""
    text = path.read_text()
    try:
        return parse_capture_text(text)
    except CaptureFormatError as e:
        raise CaptureFormatError(f"{path.name}: {e}") from e


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

    Repeated polls are folded (:mod:`canlib.captures.folding`), and the document is
    pretty-printed with ``indent=2`` except that each capture and each ``state_spans``
    span is one line: a session's metadata stays readable while a long recording costs a
    line per stored capture, not one per field.
    """
    return _render(fold_file(data), 0, ()) + "\n"


# Lists whose items each sit on one line, keyed by the dict-key path from the document root
# (list indices are not part of the path), with the separators used inside that line.
_ONE_LINE_ITEMS: dict[tuple[str, ...], tuple[str, str]] = {
    ("sessions", "captures"): (",", ":"),
    ("sessions", "state_spans", "spans"): (", ", ": "),
}


def _render(value: Any, level: int, path: tuple[str, ...]) -> str:
    """``json.dumps(value, indent=2)``, with the :data:`_ONE_LINE_ITEMS` lists one item per line.

    Byte-identical to ``json.dumps(indent=2, ensure_ascii=False)`` for everything
    outside those lists.
    """
    pad, close = "  " * (level + 1), "  " * level
    if isinstance(value, dict) and value:
        items = (
            f"{pad}{json.dumps(k, ensure_ascii=False)}: " + _render(v, level + 1, (*path, k))
            for k, v in value.items()
        )
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
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
