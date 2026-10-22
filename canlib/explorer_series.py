"""Decoded capture series for the docs explorer's data view.

Captures hold raw payloads; the explorer plots decoded numbers. They are decoded here, at
docs build time, with the same evaluator every other canair command uses, so the browser never
needs a second expression engine and a refined expression re-decodes history on the next build.

Only decoded numeric values of signals defined in the profile leave this module. Never a
payload, a scan result, an identity response or a capture without a timestamp. Output is one
small eager index plus one lazily-fetched document per session.

Plan: ``plans/2026-10-22-explorer-capture-plots.md``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import Any

from . import capture_io
from .decoding import decode_param_rows
from .pids import load_pids, pid_status
from .state_spans import parse_time_key

# Sessions with fewer numeric samples than this are not worth a plot and are left out.
MIN_SAMPLES = 10
SIGNIFICANT_DIGITS = 6
_DAY_MS = 86_400_000
_HALF_DAY_MS = _DAY_MS // 2


def rx_by_address(exported: Mapping[str, Any]) -> dict[int, str]:
    """Response address -> ECU name, from an ``export_profile`` result."""
    return {int(e["rx_id"], 16): e["name"] for e in exported["ecus"] if e.get("rx_id")}


def _number(value: Any) -> float | int | None:
    """A JSON-friendly rounded number, or None for anything that is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    rounded = float(f"{value:.{SIGNIFICANT_DIGITS}g}")
    return int(rounded) if rounded.is_integer() and abs(rounded) < 1e15 else rounded


class _Clock:
    """Absolute milliseconds for ``HH:MM:SS`` times, carried across a midnight rollover.

    A session's captures are not always in chronological order (batched reads are stored per
    PID), so the origin is the earliest time seen, decided once all of them have been read.
    """

    def __init__(self) -> None:
        self._last_ms = 0.0
        self._days = 0

    def __call__(self, raw: Any) -> float | None:
        seconds = parse_time_key(raw)
        if seconds is None:
            return None
        ms = seconds * 1000.0 + self._days * _DAY_MS
        if ms < self._last_ms - _HALF_DAY_MS:
            self._days += 1
            ms += _DAY_MS
        self._last_ms = max(self._last_ms, ms)
        return ms


def _clock_label(origin_ms: float) -> str:
    s = int(origin_ms / 1000) % 86_400
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _upper(states: Any) -> list[str]:
    return [str(s).upper() for s in states or []]


def _spans(session: Mapping[str, Any], clock: _Clock, origin_ms: float) -> list[dict[str, Any]]:
    block = session.get("state_spans")
    raw = block.get("spans") if isinstance(block, Mapping) else None
    spans = []
    for span in raw or []:
        at = clock(span.get("at")) if isinstance(span, Mapping) else None
        if at is not None:
            spans.append({"t": max(round(at - origin_ms), 0), "states": _upper(span.get("states"))})
    if not spans and len(_upper(session.get("vehicle_states"))) == 1:
        spans = [{"t": 0, "states": _upper(session.get("vehicle_states"))}]
    return spans


def _parameters(ecu_def: Mapping, pid: str) -> dict | None:
    for key, pid_def in (ecu_def.get("pids") or {}).items():
        if str(key) == pid and isinstance(pid_def, Mapping):
            if pid_status(dict(pid_def)) == "ignored" or not pid_def.get("parameters"):
                return None
            return dict(pid_def["parameters"])
    return None


def _stream(ecu: str, pid: str, times: list[int], rows: list[dict]) -> dict[str, Any] | None:
    """One ``ECU:PID`` stream. A signal that never changes is stored as a single number."""
    sig: dict[str, Any] = {}
    for name in sorted({n for row in rows for n in row}):
        values = [row.get(name) for row in rows]
        present = {v for v in values if v is not None}
        if present:
            sig[name] = next(iter(present)) if len(present) == 1 and None not in values else values
    if not sig:
        return None
    deltas = [times[0], *(b - a for a, b in pairwise(times))]
    return {"ecu": ecu, "pid": pid, "t": deltas, "sig": sig}


def _session(
    session: Mapping[str, Any], ecus: Mapping[str, Mapping], rx_names: Mapping[int, str]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(index entry without its id, full document) for one session; None if not plottable."""
    clock = _Clock()
    columns: dict[tuple[str, str], list[tuple[float, dict]]] = {}
    for cap in session.get("captures") or []:
        if not cap.get("payload") or not cap.get("time"):
            continue
        t = clock(cap["time"])
        try:
            ecu = rx_names.get(int(str(capture_io.capture_rx(cap)), 16), "")
        except ValueError:
            continue
        params = _parameters(ecus[ecu], str(cap.get("pid"))) if ecu in ecus else None
        if params is None or t is None:
            continue
        row = {r[0]: _number(r[1]) for r in decode_param_rows(cap["payload"], params)}
        columns.setdefault((ecu, str(cap["pid"])), []).append((t, row))
    if not columns:
        return None
    origin = min(t for points in columns.values() for t, _ in points)

    streams, summary, samples = [], {}, 0
    for (ecu, pid), points in columns.items():
        points.sort(key=lambda p: p[0])
        times = [round(t - origin) for t, _ in points]
        if stream := _stream(ecu, pid, times, [row for _, row in points]):
            streams.append(stream)
            samples += len(times)
            summary[f"{ecu}:{pid}"] = {
                "n": len(times),
                "sig": sorted(stream["sig"]),
                "vary": sorted(n for n, v in stream["sig"].items() if isinstance(v, list)),
            }
    if samples < MIN_SAMPLES:
        return None

    duration = max(sum(s["t"]) for s in streams)
    date = str(session.get("date", ""))
    start = _clock_label(origin)
    doc: dict[str, Any] = {"t0": f"{date}T{start}", "dur": duration, "streams": streams}
    if spans := _spans(session, clock, origin):
        doc["spans"] = spans
    entry = {
        "date": date,
        "start": start,
        "dur": duration,
        "label": session.get("label", ""),
        "states": _upper(session.get("vehicle_states")),
        "samples": samples,
        "streams": summary,
    }
    entry.update(
        {k: session[k] for k in ("notes", "transport", "keep_mode", "quality") if session.get(k)}
    )
    return entry, doc


def export_sessions(
    root: Path, rx_names: Mapping[int, str]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """``(index, documents)`` for every plottable session of the profile at ``root``.

    ``documents`` maps a session id (``<date>-<n>``, ``n`` being the session's position among
    that day's sessions, so it is stable while capture files are append-only) to its document.
    """
    captures_dir = root / "captures"
    if not captures_dir.is_dir() or not (root / "ecus").is_dir():
        return [], {}
    ecus = {str(k): v for k, v in (load_pids(root / "ecus").get("ecus") or {}).items()}

    index: list[dict[str, Any]] = []
    documents: dict[str, dict[str, Any]] = {}
    position: defaultdict[str, int] = defaultdict(int)
    for path in capture_io.iter_capture_files(captures_dir):
        for session in (capture_io.load_capture_file(path) or {}).get("sessions") or []:
            date = str(session.get("date") or path.stem)
            session_id = f"{date}-{position[date]:02d}"
            position[date] += 1
            if built := _session(session, ecus, rx_names):
                index.append({"id": session_id, **built[0]})
                documents[session_id] = built[1]
    return index, documents
