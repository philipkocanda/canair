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
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .capture_types import CaptureFile

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
    from .profile import active

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
    from .profile import active

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


def load_capture_file(path: Path) -> CaptureFile:
    """Parse one capture file (JSON). Returns the decoded structure (a dict)."""
    return json.loads(path.read_text())


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


def dump_capture_file(path: Path, data: Any) -> None:
    """Write ``data`` to ``path`` as pretty JSON, atomically.

    ``indent=2`` + ``ensure_ascii=False`` keep the file git-diffable and readable
    for review (the tree is public); insertion order is preserved (no
    ``sort_keys``) so the builder's field grouping and diffs stay stable. Written
    via a temp file + ``os.replace`` so a crash never leaves a half-written file.
    """
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
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
