"""Raw broadcast-CAN frame-log store (domain B).

The counterpart to the diagnostic ``captures/*.yaml`` store, for *passively
observed* CAN frames (``arb_id, data[], timestamp``) that no diagnostic request
elicits. Frame logs are **high-volume**, so — unlike UDS captures — they are NOT
exploded into the YAML capture schema: the original log files are kept natively
under ``<profile>/captures/can/`` and only *indexed* by ``captures/can/index.yaml``
(schema ``canlib/schema/can_index_schema.json``).

This module is the log-store layer: read/summarise a frame log (via python-can's
readers), import one into the store, and list what's indexed. Frame *signal
extraction* (bit/byte fields → named signals) is a separate concern that lands
with the analysis-seam work (Stage 2+). See
``plans/2026-07-24-raw-can-analysis.md``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .profile import Profile

# On-disk log formats we can read into the store. GVRET (SavvyCAN CSV) has a
# custom adapter here; `csv` is python-can's own CSV. Both use the .csv extension,
# so `detect_format` content-sniffs the header to tell them apart on auto.
SUPPORTED_FORMATS = ("asc", "blf", "csv", "log", "trc", "gvret")
_EXT_TO_FORMAT = {
    ".asc": "asc",
    ".blf": "blf",
    ".csv": "csv",  # may be re-resolved to gvret by header sniff
    ".log": "log",  # candump
    ".trc": "trc",
}
# SavvyCAN GVRET CSV header (microsecond timestamps, hex ID, D1..D8 data columns).
_GVRET_HEADER_TOKENS = ("time stamp", "id", "extended", "len", "d1")


class CanLogError(Exception):
    """A raw-CAN log could not be read or imported."""


@dataclass
class FrameLogSummary:
    """What a scan of a frame log yields, before it's written to the index."""

    frame_count: int = 0
    id_set: list[str] = field(default_factory=list)  # sorted hex arbitration IDs
    first_timestamp: float | None = None
    last_timestamp: float | None = None


def detect_format(path: Path, explicit: str | None = None) -> str:
    """Resolve the log format from ``explicit`` (``--format``) or the extension.

    For a ``.csv`` on auto, sniffs the header to distinguish SavvyCAN **GVRET**
    from python-can CSV. Raises :class:`CanLogError` on an unrecognised extension.
    """
    if explicit and explicit != "auto":
        if explicit not in SUPPORTED_FORMATS:
            raise CanLogError(
                f"unknown format {explicit!r} (one of {', '.join(SUPPORTED_FORMATS)})"
            )
        return explicit
    fmt = _EXT_TO_FORMAT.get(path.suffix.lower())
    if fmt == "csv" and _looks_like_gvret(path):
        return "gvret"
    if fmt not in SUPPORTED_FORMATS:
        raise CanLogError(
            f"cannot determine log format for {path.name!r} "
            f"(use --format {{{','.join(SUPPORTED_FORMATS)}}}); got extension {path.suffix!r}"
        )
    return fmt


def _reader(path: Path, fmt: str):
    """A python-can reader for ``fmt`` (context manager yielding ``can.Message``)."""
    import can

    readers = {
        "asc": "ASCReader",
        "blf": "BLFReader",
        "csv": "CSVReader",
        "log": "CanutilsLogReader",  # candump
        "trc": "TRCReader",
    }
    return getattr(can, readers[fmt])(str(path))


def scan_log(path: Path, fmt: str) -> FrameLogSummary:
    """Read every frame once to summarise it (count, distinct IDs, time span).

    Distinct arbitration IDs are rendered as ``0x``-hex (11- or 29-bit).
    """
    summary = FrameLogSummary()
    ids: set[int] = set()
    for msg in iter_frames(path, fmt):
        summary.frame_count += 1
        ids.add(msg.arbitration_id)
        ts = getattr(msg, "timestamp", None)
        if ts:
            if summary.first_timestamp is None:
                summary.first_timestamp = ts
            summary.last_timestamp = ts
    summary.id_set = [f"0x{i:X}" for i in sorted(ids)]
    return summary


def iter_frames(path: Path, fmt: str):
    """Yield every ``can.Message`` in ``path`` (format ``fmt``), once.

    Wraps python-can's readers (and the GVRET adapter) behind one seam so both
    :func:`scan_log` and the frame-series analysis layer read logs identically.
    Parse/format errors are re-raised as :class:`CanLogError`.
    """
    if fmt == "gvret":
        yield from _iter_gvret(path)
        return
    try:
        with _reader(path, fmt) as reader:
            yield from reader
    except CanLogError:
        raise
    except Exception as e:  # python-can raises assorted parse errors
        raise CanLogError(f"failed to read {path.name} as {fmt}: {e}") from e


def _looks_like_gvret(path: Path) -> bool:
    """True if ``path``'s first line is a SavvyCAN GVRET CSV header."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            header = f.readline().lower()
    except OSError:
        return False
    cols = {c.strip() for c in header.split(",")}
    return all(tok in cols for tok in _GVRET_HEADER_TOKENS)


def _iter_gvret(path: Path):
    """Parse a SavvyCAN GVRET CSV into ``can.Message`` records.

    Columns: ``Time Stamp`` (microseconds), ``ID`` (hex), ``Extended``, ``Dir``,
    ``Bus``, ``LEN``, ``D1``..``D8`` (hex data bytes). Timestamps are microseconds
    (converted to seconds); only the first ``LEN`` data columns are used.
    """
    import csv

    import can

    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or not all(
                tok in {c.strip().lower() for c in reader.fieldnames}
                for tok in _GVRET_HEADER_TOKENS
            ):
                raise CanLogError(f"{path.name} is not a SavvyCAN GVRET CSV (unexpected header)")
            # Map lower-cased header -> actual key (SavvyCAN uses 'Time Stamp', 'ID', …).
            key = {c.strip().lower(): c for c in reader.fieldnames}
            ts_k, id_k, ext_k, len_k = key["time stamp"], key["id"], key["extended"], key["len"]
            data_ks = [key[f"d{i}"] for i in range(1, 9) if f"d{i}" in key]
            for row in reader:
                try:
                    ts_us = int(row[ts_k])
                    arb_id = int(row[id_k], 16)
                    dlc = int(row[len_k])
                    data = bytes(int(row[dk], 16) for dk in data_ks[:dlc] if row.get(dk))
                except (ValueError, TypeError, KeyError):
                    continue  # skip malformed rows
                ext = str(row.get(ext_k, "")).strip().lower() in ("true", "1")
                yield can.Message(
                    arbitration_id=arb_id,
                    data=data,
                    dlc=dlc,
                    is_extended_id=ext,
                    timestamp=ts_us / 1_000_000.0,
                )
    except CanLogError:
        raise
    except OSError as e:
        raise CanLogError(f"failed to read {path.name} as gvret: {e}") from e


def _epoch_to_date(ts: float | None) -> str | None:
    """A log's date from an *absolute* first-frame epoch, or None for relative ts.

    Many logs use relative timestamps (start at 0); only treat a plausibly
    absolute epoch (>= 2001-09-09) as a date so the index stays deterministic.
    """
    if ts and ts > 1_000_000_000:
        return _date.fromtimestamp(ts).isoformat()
    return None


@dataclass
class ImportedLog:
    """The index entry written for a freshly-imported frame log."""

    entry: dict
    stored_path: Path
    summary: FrameLogSummary


def import_log(
    source_path: Path,
    profile: Profile,
    *,
    fmt: str | None = None,
    label: str | None = None,
    vehicle_states: list[str] | None = None,
    notes: str | None = None,
    bitrate: int | None = None,
    source: str | None = None,
    date: str | None = None,
    force: bool = False,
) -> ImportedLog:
    """Copy a frame log into the profile's ``captures/can/`` store and index it.

    The file is stored **verbatim** (no re-encoding); its metadata is appended to
    ``captures/can/index.yaml``. Raises :class:`CanLogError` on a missing source,
    an unreadable/empty log, or a name collision (unless ``force``).
    """
    if not source_path.is_file():
        raise CanLogError(f"no such file: {source_path}")
    resolved_fmt = detect_format(source_path, fmt)
    summary = scan_log(source_path, resolved_fmt)
    if summary.frame_count == 0:
        raise CanLogError(f"{source_path.name} contains no CAN frames")

    can_dir = profile.can_dir
    can_dir.mkdir(parents=True, exist_ok=True)
    dest = can_dir / source_path.name
    if dest.exists() and not force:
        raise CanLogError(
            f"{dest.name} already exists in the store — pass --force to overwrite, "
            "or rename the source file"
        )
    shutil.copyfile(source_path, dest)

    entry: dict = {"file": source_path.name, "format": resolved_fmt}
    resolved_date = date or _epoch_to_date(summary.first_timestamp)
    if resolved_date:
        entry["date"] = resolved_date
    if source:
        entry["source"] = source
    if label:
        entry["label"] = label
    if vehicle_states:
        entry["vehicle_states"] = list(vehicle_states)
    if notes:
        entry["notes"] = notes
    entry["frame_count"] = summary.frame_count
    if summary.id_set:
        entry["id_set"] = summary.id_set
    if bitrate:
        entry["bitrate"] = bitrate

    _append_index_entry(profile.can_index_file, entry, replace_file=dest.name if force else None)
    return ImportedLog(entry=entry, stored_path=dest, summary=summary)


def _append_index_entry(index_path: Path, entry: dict, *, replace_file: str | None = None) -> None:
    """Append (or replace, when re-importing) an entry in ``index.yaml``.

    Comment-preserving via ``yaml_rt`` so a hand-annotated index survives edits.
    """
    from ruamel.yaml.comments import CommentedMap

    from .yaml_rt import dump as _dump
    from .yaml_rt import round_trip_yaml as _yaml

    data = None
    if index_path.exists():
        with open(index_path) as f:
            data = _yaml().load(f)
    if not isinstance(data, dict):
        data = CommentedMap()
    logs = data.get("logs")
    if not isinstance(logs, list):
        logs = []
        data["logs"] = logs
    if replace_file is not None:
        for i, existing in enumerate(logs):
            if isinstance(existing, dict) and existing.get("file") == replace_file:
                logs[i] = entry
                break
        else:
            logs.append(entry)
    else:
        logs.append(entry)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "w") as f:
        _dump(data, f)


def load_index(profile: Profile) -> dict:
    """The parsed ``captures/can/index.yaml`` (``{}`` when absent)."""
    from canlib import yaml_io

    path = profile.can_index_file
    if not path.exists():
        return {}
    return yaml_io.safe_load(path.read_text()) or {}


def list_logs(profile: Profile) -> list[dict]:
    """Indexed frame logs (the ``logs:`` list), or ``[]`` when none."""
    logs = load_index(profile).get("logs")
    return logs if isinstance(logs, list) else []
