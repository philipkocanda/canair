"""Capture journaling — a write-ahead log for streaming/one-shot captures.

Problem: ``canair query --save`` (especially ``--monitor``) buffers all payloads
in memory and only writes the capture file on a clean exit. A crash, ``kill``, or
dropped connection loses the whole session.

Solution: as payloads stream in, append them to an append-only JSONL *journal*
sidecar under ``captures/.journal/``, flushed (and fsync'd) per write. On a clean
exit the journal is *reconciled* — its records are folded into a single session
appended to ``captures/YYYY-MM-DD.yaml`` (via the same builders used elsewhere),
and the journal file is deleted. If the process dies uncleanly the journal
survives and can be recovered later with ``canair captures --recover``.

Journal format (one JSON object per line):

    {"v": 1, "type": "meta", "date": "...", "label": "...", "vehicle_states": [...],
     "notes": "...", "source": "monitor", "keep_mode": "unique"}
    {"type": "capture", "ecu": "0x7EC", "pid": "2101", "payload": "6101...",
     "date": "2026-07-22", "time": "12:00:01"}
    ...

Each ``capture`` row carries its own ``date`` (the acquisition date), so a
session that spans midnight reconciles into the correct per-day capture files
rather than being lumped under a single date fixed at reconcile time. The
``meta`` ``date`` (session start) is the fallback for rows written before
per-record dates existed.

Multiple ``meta`` lines may appear (metadata edited mid-session); reconcile uses
the **last** one. For one-shot producers that already build a full session dict
(scan/raw/discover), a single ``{"type": "session", "session": {...}}`` line is
written instead of ``capture`` lines.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

JOURNAL_VERSION = 1
JOURNAL_DIRNAME = ".journal"
JOURNAL_SUFFIX = ".jsonl"


def _journal_dir(captures_dir: Path) -> Path:
    return captures_dir / JOURNAL_DIRNAME


class CaptureJournal:
    """Append-only write-ahead log for a single capture session.

    Open with :meth:`open`, stream rows with :meth:`append` (or a whole session
    with :meth:`append_session`), then :meth:`reconcile` on clean exit — or use
    it as a context manager, which reconciles on a clean ``__exit__`` and leaves
    the journal in place if the block raised (so it can be recovered).
    """

    def __init__(self, path: Path, captures_dir: Path):
        self.path = path
        self.captures_dir = captures_dir
        self._fh = None
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def open(
        cls,
        captures_dir: Path,
        *,
        label: str | None = None,
        vehicle_states: list | None = None,
        notes: str | None = None,
        source: str = "query",
        keep_mode: str | None = None,
    ) -> CaptureJournal:
        """Create a fresh journal under ``captures_dir/.journal/`` and write meta."""
        jdir = _journal_dir(captures_dir)
        jdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        # Include PID for uniqueness if two runs start in the same second.
        stem = f"{ts}-{os.getpid()}"
        path = jdir / f"{stem}{JOURNAL_SUFFIX}"
        n = 1
        while path.exists():
            path = jdir / f"{stem}-{n}{JOURNAL_SUFFIX}"
            n += 1
        journal = cls(path, captures_dir)
        journal._fh = open(path, "a", encoding="utf-8")
        journal._write(
            {
                "v": JOURNAL_VERSION,
                "type": "meta",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "label": label or "",
                "vehicle_states": list(vehicle_states or []),
                "notes": notes or "",
                "source": source,
                "keep_mode": keep_mode,
            },
            durable=True,
        )
        return journal

    def _write(self, record: dict, *, durable: bool = False) -> None:
        assert self._fh is not None
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        if durable:
            self.flush()

    def flush(self) -> None:
        """Flush buffered records durably (flush + ``fsync``).

        Streaming :meth:`append` is buffered (no per-record fsync); the monitor
        calls this once per poll cycle instead, so N payloads cost one ``fsync``
        rather than N syncs on the event loop. Worst-case loss on a hard crash is
        the last (~1 cycle) of appends; clean exit / ``__exit__`` reconciles all.
        """
        if self._fh is None or self._fh.closed:
            return
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass

    # -- streaming API -----------------------------------------------------

    def append(self, ecu_ref: str, pid: str, hex_val: str, time: str = "", date: str = "") -> None:
        """Append one captured payload row (buffered; caller flushes per cycle).

        Payload rows are time-series samples, so each stamps both a ``date`` and
        a ``time`` (the moment the response arrived). The per-record ``date`` is
        what lets a session spanning midnight reconcile into the correct per-day
        capture files — the session's date is no longer a single value fixed at
        reconcile time. Callers pass the acquisition timestamp; both fall back to
        "now" when omitted.
        """
        rec: dict = {"type": "capture", "ecu": ecu_ref, "pid": pid, "payload": hex_val.upper()}
        now = datetime.now()
        rec["date"] = date or now.strftime("%Y-%m-%d")
        rec["time"] = time or now.strftime("%H:%M:%S")
        self._write(rec)

    def append_session(self, session: dict) -> None:
        """Append a fully-built session dict (one-shot scan/raw/discover)."""
        self._write({"type": "session", "session": session}, durable=True)

    def update_meta(
        self,
        label: str | None = None,
        vehicle_states: list | None = None,
        notes: str | None = None,
    ) -> None:
        """Append a meta record with the provided fields (last-wins on reconcile).

        Only non-None fields are written, so a partial update (e.g. states only)
        leaves the previously-recorded label/notes intact.
        """
        rec: dict = {"type": "meta"}
        if label is not None:
            rec["label"] = label
        if vehicle_states is not None:
            rec["vehicle_states"] = list(vehicle_states)
        if notes is not None:
            rec["notes"] = notes
        self._write(rec, durable=True)

    def _close_fh(self) -> None:
        if self._fh is not None and not self._fh.closed:
            self._fh.close()
        self._closed = True

    # -- reconcile ---------------------------------------------------------

    def reconcile(self, keep_mode: str | None = None) -> Path | None:
        """Fold the journal into a dated capture file, then delete the journal.

        Returns the capture file path, or None if there was nothing to save.
        """
        self._close_fh()
        result = reconcile_file(self.path, keep_mode=keep_mode)
        return result

    def discard(self) -> None:
        """Close and delete the journal without saving (e.g. user cancelled)."""
        self._close_fh()
        self.path.unlink(missing_ok=True)

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> CaptureJournal:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Clean exit → reconcile. Exception → leave the journal for recovery.
        if exc_type is None:
            self.reconcile()
        else:
            self._close_fh()
        return False


# ---------------------------------------------------------------------------
# Reconciliation (shared by live reconcile + recovery)
# ---------------------------------------------------------------------------


def _read_records(path: Path) -> list[dict]:
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # Tolerate a truncated final line from an unclean kill.
                continue
    return records


def _dedup(
    rows: list[tuple[str, str, str, str, str]], keep_mode: str | None
) -> list[tuple[str, str, str, str, str]]:
    """Apply keep-mode dedup to (ecu, pid, hex, time, date) rows, preserving order.

    ``None``/``last`` keep every row as-is; ``unique`` drops rows whose
    (ecu, pid, payload) has already been seen.
    """
    if keep_mode not in ("unique",):
        return rows
    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[str, str, str, str, str]] = []
    for row in rows:
        key = (row[0], row[1], row[2])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def build_session_from_records(
    records: list[dict], keep_mode: str | None = None, recovered: bool = False
) -> list[dict]:
    """Build capture session dicts from journal records — one per capture date.

    Uses the last ``meta`` record for label/vehicle_states/notes and its
    ``keep_mode`` unless ``keep_mode`` is passed explicitly. Payload rows are
    grouped by their per-record ``date`` so a session spanning midnight yields
    one session per calendar day (each landing in the correct ``YYYY-MM-DD.yaml``
    on save); rows missing a date fall back to the ``meta`` date. Returns an
    empty list when the journal has no capture/session payloads.
    """
    from .captures import build_query_session

    meta = {"label": "", "vehicle_states": [], "notes": "", "keep_mode": None, "date": ""}
    session_records: list[dict] = []
    rows: list[tuple[str, str, str, str, str]] = []
    meta_date = ""
    for rec in records:
        rtype = rec.get("type")
        if rtype == "meta":
            for k in ("label", "vehicle_states", "notes", "keep_mode", "date"):
                if k in rec:
                    meta[k] = rec[k]
            if rec.get("date"):
                meta_date = str(rec["date"])
        elif rtype == "session":
            session_records.append(rec["session"])
        elif rtype == "capture":
            rows.append(
                (
                    rec.get("ecu", ""),
                    rec.get("pid", ""),
                    rec.get("payload", ""),
                    rec.get("time", ""),
                    str(rec.get("date") or meta_date),
                )
            )

    # meta values come from a loosely-typed journal dict; the label/notes fields
    # are always strings at runtime, so coerce to satisfy build_query_session.
    label = str(meta.get("label") or "Recovered session")
    vehicle_states = list(meta.get("vehicle_states") or [])
    notes = str(meta.get("notes") or "")
    if recovered:
        notes = f"{notes} [recovered]".strip()
    _keep = meta.get("keep_mode")
    if keep_mode is not None:
        effective_keep: str | None = keep_mode
    else:
        effective_keep = _keep if isinstance(_keep, str) else None

    # One-shot producer stored a complete session; merge its captures in. These
    # carry their own session date, so they are not day-split here.
    if session_records:
        # Only the first session dict carries the base; append others' captures.
        base = dict(session_records[0])
        base["label"] = label
        if vehicle_states:
            base["vehicle_states"] = vehicle_states
        elif "vehicle_states" in base:
            del base["vehicle_states"]
        if notes:
            base["notes"] = notes
        elif "notes" in base:
            del base["notes"]
        for extra in session_records[1:]:
            base.setdefault("captures", []).extend(extra.get("captures", []))
        return [base]

    if not rows:
        return []

    rows = _dedup(rows, effective_keep)

    # Group by capture date so each day becomes its own session. Preserve the
    # order dates first appear so the earliest day is saved first.
    by_date: dict[str, list[tuple[str, str, str, str]]] = {}
    for ecu, pid, hex_val, ts, rdate in rows:
        by_date.setdefault(rdate, []).append((ecu, pid, hex_val, ts))

    sessions: list[dict] = []
    for rdate, day_rows in by_date.items():
        sessions.append(
            build_query_session(
                day_rows,
                label,
                vehicle_states,
                notes,
                keep_mode=effective_keep,
                date=rdate or None,
            )
        )
    return sessions


def reconcile_file(
    path: Path, keep_mode: str | None = None, recovered: bool = False
) -> Path | None:
    """Reconcile a single journal file into its captures dir, then delete it.

    The captures dir is the journal's grandparent (``.../captures/.journal/x`` →
    ``.../captures``). A journal spanning midnight yields one session per day,
    each saved to its own ``YYYY-MM-DD.yaml``. Returns the last capture file
    path written (they land in per-day files), or None if empty.
    """
    from .captures import save_session

    if not path.exists():
        return None
    captures_dir = path.parent.parent
    records = _read_records(path)
    sessions = build_session_from_records(records, keep_mode=keep_mode, recovered=recovered)
    sessions = [s for s in sessions if s and s.get("captures")]
    if not sessions:
        # Nothing worth keeping — drop the journal.
        path.unlink(missing_ok=True)
        return None
    written: Path | None = None
    for session in sessions:
        written = save_session(session, captures_dir)
    path.unlink(missing_ok=True)
    return written


# ---------------------------------------------------------------------------
# Orphan discovery + recovery
# ---------------------------------------------------------------------------


def list_orphans(captures_dir: Path) -> list[Path]:
    """Return leftover journal files under ``captures_dir/.journal/`` (sorted)."""
    jdir = _journal_dir(captures_dir)
    if not jdir.is_dir():
        return []
    return sorted(jdir.glob(f"*{JOURNAL_SUFFIX}"))


def recover(path: Path, discard: bool = False) -> Path | None:
    """Reconcile (or ``discard``) a single orphaned journal.

    On recover, the session notes are tagged ``[recovered]``. On discard, the
    journal is deleted without saving. Returns the capture file path (recover) or
    None (discard / empty).
    """
    if discard:
        Path(path).unlink(missing_ok=True)
        return None
    return reconcile_file(Path(path), recovered=True)
