#!/usr/bin/env python3
"""Time-aligned cross-signal analysis primitives.

The building blocks for correlating signals *across* different PIDs and ECUs —
``canair correlate``, ``canair hunt``, and cross-ECU ``decode --corr``.

canair polls one connection sequentially, so different ECUs are sampled with a
small (~0.3-3 s) skew. To compare a signal on ECU A against one on ECU B we
join by **nearest timestamp within a tolerance**, using the real ``datetime``
built by :func:`capture_dates.entry_datetime`. Captures with no usable ``time``
(one-shot scan/probe/identity reads) are dropped from time joins — but retained
by value/state views elsewhere.

Nothing here talks to the device; it is pure analysis over ``captures/``.
"""

from __future__ import annotations

import bisect
import csv
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cached_property
from pathlib import Path

from .byteindex import payload_to_wican_bytes as _payload_to_wican_bytes
from .capture_dates import entry_datetime, filter_by_date_range, filter_by_text
from .capture_store import load_all_captures
from .capture_types import CaptureEntry
from .expression import evaluate_expression

__all__ = [
    "DEFAULT_JOIN_TOL_S",
    "DecodedCapture",
    "PreparedSeries",
    "SignalRef",
    "TimePoint",
    "align_many",
    "extract_series",
    "join_indices",
    "join_nearest",
    "join_nearest_presorted",
    "join_nearest_triple",
    "join_prepared",
    "load_reference_file",
    "load_signal_captures",
    "mirror_aligned_count",
    "prepare_series",
    "series_time_ranges_disjoint",
    "timestamps_disjoint",
]

# Default nearest-neighbour join window (shared by align/correlate/hunt/xanalysis).
# The sequential single-connection poller visits ECUs round-robin, so two ECUs'
# samples are skewed by the time to poll everything in between. On a large
# multi-ECU monitor session (e.g. 8 ECUs) adjacent-in-cycle ECUs land ~3.4 s
# apart, which a 2.5 s window silently dropped — a "far" ECU joined zero rows.
# 5 s covers the observed skew while still being "nearest" (a closer sample, when
# one exists, always wins); widen further with --join-tol for sparse/keep:unique
# scopes.
DEFAULT_JOIN_TOL_S = 5.0

# A time gap larger than this between consecutive samples of one signal marks a
# new recording *session* for per-session detrending (recordings are minutes to
# days apart; within a session samples arrive every few seconds). Deliberately
# generous so a brief mid-drive stall doesn't split a session.
DEFAULT_SESSION_GAP_S = 300.0


@dataclass(frozen=True)
class SignalRef:
    """A reference to one decodable signal: ``ECU:PID:PARAM`` or ``ECU:PID:EXPR``.

    ``name_or_expr`` is either a defined parameter name (resolved against the
    PID's ``parameters``) or a raw WiCAN expression (e.g. ``[S10:S11]``,
    ``B22``). :func:`extract_series` decides which by asking the caller-supplied
    resolver, falling back to treating it as an expression.
    """

    ecu: str
    pid: str
    name_or_expr: str

    @property
    def label(self) -> str:
        return f"{self.ecu}:{self.pid}:{self.name_or_expr}"

    @classmethod
    def parse(cls, spec: str) -> SignalRef:
        """Parse ``ECU:PID:PARAM_OR_EXPR``.

        The expression part may itself contain colons (``[S10:S11]``), so we
        split only on the first two colons and keep the remainder intact.
        """
        parts = spec.split(":", 2)
        if len(parts) != 3 or not all(p.strip() for p in parts[:2]) or not parts[2].strip():
            raise ValueError(
                f"invalid signal reference {spec!r} "
                "(expected ECU:PID:PARAM or ECU:PID:EXPR, e.g. ESC:22C101:REAL_SPEED_KMH)"
            )
        return cls(parts[0].strip(), parts[1].strip(), parts[2].strip())


@dataclass
class TimePoint:
    """One decoded sample: a timestamp and a numeric value."""

    dt: datetime
    value: float


def detrend_by_session(
    series: list[TimePoint], gap_s: float = DEFAULT_SESSION_GAP_S
) -> list[TimePoint]:
    """Subtract each recording session's mean from a time series.

    A slowly-varying absolute level (pack/12V/mains voltage, a temperature held
    near a setpoint) is dominated by its per-recording DC baseline, so Pearson
    correlation across sessions ranks that cross-session offset rather than the
    in-session variation that actually tracks a reference. Splitting the series
    wherever consecutive samples jump by more than ``gap_s`` (recordings sit
    minutes-to-days apart) and removing each segment's mean leaves only the
    within-session swing — making a level signal rankable by ``--against`` /
    ``correlate`` instead of merely warned about.

    The timestamps are preserved (only values shift), so a subsequent
    nearest-timestamp join is unaffected. A segment with <2 points is left as-is.
    Label-independent: it keys off timestamps, so it works even when session
    labels are absent/scrubbed.
    """
    if not series:
        return []
    ordered = sorted(series, key=lambda tp: tp.dt)
    out: list[TimePoint] = []
    seg: list[TimePoint] = [ordered[0]]
    for tp in ordered[1:]:
        if (tp.dt - seg[-1].dt).total_seconds() > gap_s:
            out.extend(_demean_segment(seg))
            seg = [tp]
        else:
            seg.append(tp)
    out.extend(_demean_segment(seg))
    return out


def _demean_segment(seg: list[TimePoint]) -> list[TimePoint]:
    if len(seg) < 2:
        return list(seg)
    mean = sum(tp.value for tp in seg) / len(seg)
    return [TimePoint(tp.dt, tp.value - mean) for tp in seg]


@dataclass(frozen=True)
class DecodedCapture:
    """One capture reduced to the form every analysis pass actually reads.

    ``frame`` is the reconstructed WiCAN frame (ISO-TP PCI bytes re-inserted, via
    :func:`byteindex.payload_to_wican_bytes`), ``dt`` the capture's timestamp
    (:func:`capture_dates.entry_datetime`), and ``payload`` the original hex —
    kept because the length-aware byte-role helpers
    (:func:`byteindex.mappable_data_indices`) need the raw payload, not the frame.
    """

    dt: datetime | None
    frame: bytes
    payload: str


@dataclass
class LoadedPid:
    """All captures for one (ecu, pid), plus loader metadata."""

    ecu: str
    pid: str
    captures: list[CaptureEntry] = field(default_factory=list)  # payload captures only
    n_no_time: int = 0  # payload captures dropped for lacking a usable time

    @cached_property
    def decoded(self) -> list[DecodedCapture]:
        """Every capture's timestamp + reconstructed WiCAN frame, decoded **once**.

        Each analysis pass over a PID reads the same two derived values from every
        capture: the timestamp and the PCI-reinserted frame. Deriving them at each
        call site made the work O(passes x captures) — and a pass is per *series*,
        so ``investigate --bits`` (hundreds of byte/bit series over one PID) re-ran
        the same hex decode and timestamp parse hundreds of thousands of times.
        :func:`build_byte_series` already hoisted it within its own body; this
        lifts it to the object so every consumer shares one decode.

        Captures whose payload is missing or not valid hex are dropped (the store
        can legitimately hold a stored ``NO DATA`` or a mis-transcribed capture);
        that mirrors what every call site did individually. Computed lazily, so a
        consumer that only wants ``captures`` pays nothing.
        """
        out: list[DecodedCapture] = []
        for cap in self.captures:
            payload = cap.get("payload")
            if not payload:
                continue
            try:
                frame = _payload_to_wican_bytes(payload)
            except Exception:
                continue
            out.append(DecodedCapture(entry_datetime(cap), frame, str(payload)))
        return out

    def timed_frames(self) -> list[tuple[datetime, bytes]]:
        """``(timestamp, frame)`` for every decodable capture that has a time.

        The input shape for time-joined work (byte/bit series, byte hunts). An
        untimed capture can't participate in a nearest-timestamp join, so it is
        excluded — see :func:`load_signal_captures`, which counts those in
        ``n_no_time`` and already keeps them out of ``captures``.
        """
        return [(d.dt, d.frame) for d in self.decoded if d.dt is not None]


def load_signal_captures(
    specs: list[tuple[str, str]],
    *,
    since: date | datetime | None = None,
    until: date | datetime | None = None,
    state: str | None = None,
    label: str | None = None,
    captures_dir: Path | None = None,
) -> dict[tuple[str, str], LoadedPid]:
    """Load ``payload`` captures grouped by ``(ecu, pid)`` for a set of specs.

    ``specs`` is a list of ``(ECU, PID)`` (canonical short names, upper-cased for
    matching). Reuses the single canonical loader
    (:func:`capture_store.load_all_captures`) and the shared scope filters so
    date/state/label narrowing behaves identically to ``decode``/``captures``.

    Scan/probe captures (``scan_results``, no ``payload``) are ignored — they are
    not time-series. Payload captures with no usable ``time`` are counted in
    ``n_no_time`` and excluded (their ``datetime`` would be ``None``).
    """
    wanted = {(e.upper(), str(p).upper()) for e, p in specs}
    result: dict[tuple[str, str], LoadedPid] = {
        (e.upper(), str(p).upper()): LoadedPid(e.upper(), str(p).upper()) for e, p in specs
    }

    entries = load_all_captures(captures_dir)
    entries = filter_by_date_range(entries, since, until)
    entries = filter_by_text(entries, state=state, label=label)

    for e in entries:
        key = (str(e.get("ecu", "")).upper(), str(e.get("pid", "")).upper())
        if key not in wanted:
            continue
        if not e.get("payload"):
            continue  # scan/probe/identity capture — not a time series
        lp = result[key]
        if entry_datetime(e) is None:
            lp.n_no_time += 1
            continue
        lp.captures.append(e)
    return result


def longest_payload_len(captures) -> int | None:
    """Longest reassembled payload length (bytes) in ``captures``, or None.

    The WiCAN<->ISO-TP mapping depends on the frame layout — a single-frame
    (<=7-byte) response carries one PCI byte, a multi-frame response two plus one
    per consecutive frame — so render layers pass this to
    :func:`canlib.notation.relabel_signal` / :meth:`canlib.notation.ByteRef.from_wican`.

    The *longest* payload is used, matching ``coverage``: it is the most complete
    response seen, so it defines the widest valid byte range. Deliberately
    tolerant of odd entries (non-mappings, missing/blank payloads) — this only
    decides a display label and must never break a render.
    """
    longest = 0
    for cap in captures or ():
        payload = cap.get("payload") if hasattr(cap, "get") else None
        if payload:
            longest = max(longest, len(str(payload).replace(" ", "")) // 2)
    return longest or None


def payload_lengths(loaded: dict[tuple[str, str], LoadedPid]) -> dict[tuple[str, str], int]:
    """``{(ECU, PID): longest payload length in bytes}`` for a loaded signal set.

    The frame layout — and therefore the WiCAN↔ISO-TP mapping — depends on the
    payload's length (one PCI byte for a single frame, two plus one per
    consecutive frame for a multi-frame response). Render layers pass this to
    :func:`canlib.notation.relabel_signal` so a label names the right byte.

    The *longest* payload is used, matching ``coverage``: it is the most complete
    response seen, so it defines the widest valid byte range.
    """
    out: dict[tuple[str, str], int] = {}
    for key, lp in loaded.items():
        longest = longest_payload_len(lp.captures)
        if longest:
            out[key] = longest
    return out


def extract_series(
    loaded: LoadedPid,
    name_or_expr: str,
    *,
    parameters: dict | None = None,
) -> list[TimePoint]:
    """Decode one signal from a :class:`LoadedPid` into a time series.

    ``name_or_expr`` resolves to a defined parameter's expression when it matches
    a key in ``parameters`` (case-insensitively); otherwise it is treated as a
    raw WiCAN expression. Captures where the expression errors or yields a
    non-numeric value are skipped.
    """
    expr = name_or_expr
    if parameters:
        for pname, pdef in parameters.items():
            if pname.upper() == name_or_expr.upper():
                expr = pdef.get("expression", "") or name_or_expr
                break

    out: list[TimePoint] = []
    for dt, frame in loaded.timed_frames():
        try:
            val = evaluate_expression(expr, frame)
        except Exception:
            continue
        if isinstance(val, (int, float)):
            out.append(TimePoint(dt, float(val)))
    out.sort(key=lambda tp: tp.dt)
    return out


def _parse_reference_timestamp(raw: str) -> datetime | None:
    """Parse one external-reference timestamp cell into a **naive** datetime.

    Accepts ISO-8601 (``2026-07-22T09:06:37`` / ``…09:06:37.007`` / with a space),
    ``YYYY-MM-DD HH:MM:SS[.fff]``, and epoch seconds (``1753171597`` /
    ``1753171597.5``). Timezone-aware ISO strings are converted to local wall
    clock and made naive, because capture timestamps
    (:func:`capture_dates.entry_datetime`) are naive local — mixing the two would
    raise on comparison. Returns ``None`` when nothing parses (e.g. a header cell).
    """
    raw = raw.strip()
    if not raw:
        return None
    # Epoch seconds (bare number). A tiny/relative value still parses here and
    # simply won't align to wall-clock captures — that is the documented caveat.
    try:
        epoch = float(raw)
    except ValueError:
        pass
    else:
        try:
            return datetime.fromtimestamp(epoch)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def load_reference_file(
    path: Path | str, *, label: str | None = None
) -> tuple[list[TimePoint], str]:
    """Load an external reference series from a two-column ``timestamp,value`` file.

    The escape hatch for correlating against data that isn't on the bus — a
    calibrated meter log, a GPS speed track, a grid-voltage export. The file is a
    CSV (or any single-delimiter text) whose first two columns are a timestamp and
    a numeric value; a non-numeric header row is skipped automatically, and any
    later unparseable row is dropped. The series is fed through the *same*
    nearest-timestamp join as an in-capture ``ECU:PID:PARAM`` reference.

    **The timestamps must be on the same absolute wall clock as the captures**
    (session ``date`` + ``time``). A relative/zero-based log (many raw-CAN
    exporters start at 0) will parse but join to nothing — the caller reports the
    realised ``n`` so that shows up as ``n=0`` rather than a silent empty result.

    Returns ``(series, label)``. Raises :class:`ValueError` when the file can't be
    read or yields no usable ``(timestamp, value)`` rows.
    """
    from pathlib import Path

    p = Path(path)
    try:
        text = p.read_text()
    except OSError as e:
        raise ValueError(f"cannot read reference file {p}: {e}") from e

    out: list[TimePoint] = []
    for row in csv.reader(text.splitlines()):
        if len(row) < 2:
            continue
        dt = _parse_reference_timestamp(row[0])
        if dt is None:
            continue  # header or malformed timestamp
        try:
            value = float(row[1].strip())
        except ValueError:
            continue
        out.append(TimePoint(dt, value))

    if not out:
        raise ValueError(
            f"reference file {p} has no usable 'timestamp,value' rows "
            "(expected an absolute-clock timestamp in column 1 and a number in column 2)"
        )
    out.sort(key=lambda tp: tp.dt)
    # A log whose timestamps are all pre-2000 is almost certainly relative /
    # zero-based (many raw-CAN exporters start at t=0), which won't align to the
    # captures' absolute wall clock — warn rather than silently join to nothing.
    if out[-1].dt.year < 2000:
        import sys

        print(
            f"warning: reference file {p.name} timestamps look relative/zero-based "
            f"(latest is {out[-1].dt.isoformat()}); it must be on the captures' absolute "
            "clock or the join yields n=0.",
            file=sys.stderr,
        )
    return out, label or p.name


def join_nearest(
    ref: list[TimePoint],
    cand: list[TimePoint],
    tol_s: float = DEFAULT_JOIN_TOL_S,
) -> tuple[list[float], list[float], int]:
    """Nearest-neighbour join ``cand`` onto ``ref`` within ``tol_s`` seconds.

    For each reference point, pick the candidate whose timestamp is closest and
    within tolerance; reference points with no candidate in range are dropped.
    Returns ``(ref_values, cand_values, n)`` aligned pairs — ``n`` is the
    realised overlap, always reported so a thin join is visible.
    """
    if not ref or not cand:
        return [], [], 0
    return join_nearest_presorted(ref, sorted(cand, key=lambda tp: tp.dt), tol_s)


def join_nearest_presorted(
    ref: list[TimePoint],
    cand_sorted: list[TimePoint],
    tol_s: float = DEFAULT_JOIN_TOL_S,
) -> tuple[list[float], list[float], int]:
    """Like :func:`join_nearest`, but ``cand_sorted`` is already time-sorted.

    Hoists the per-call ``sorted(cand)`` out so a caller comparing one series
    against many (e.g. the O(n²) mirror/matrix sweeps) sorts each series once
    instead of on every pair.
    """
    if not ref or not cand_sorted:
        return [], [], 0
    return join_prepared(prepare_series(ref), prepare_series(cand_sorted), tol_s)


@dataclass
class PreparedSeries:
    """A time series flattened to parallel float arrays for fast joins.

    ``datetime`` arithmetic (``timedelta.total_seconds()``) dominates the O(n²)
    pairwise join sweeps (``correlate_matrix``, the mirror/overlap scans): the
    same series is subtracted against many others, and each subtraction builds a
    ``timedelta`` then converts it. Converting each timestamp to POSIX epoch
    *seconds* once lets the inner join use plain float subtraction + a float
    ``bisect``, so a whole-corpus ``correlate`` drops from ~30 s to a few
    seconds. ``ts`` is sorted ascending and parallel to ``values``.
    """

    ts: list[float]  # POSIX epoch seconds, sorted ascending
    values: list[float]


def prepare_series(points: list[TimePoint]) -> PreparedSeries:
    """Sort ``points`` by time and flatten to epoch-seconds + value arrays."""
    ordered = sorted(points, key=lambda tp: tp.dt)
    return PreparedSeries(
        ts=[tp.dt.timestamp() for tp in ordered],
        values=[tp.value for tp in ordered],
    )


def timestamps_disjoint(a_ts: Sequence[float], b_ts: Sequence[float], tol_s: float) -> bool:
    """True when two epoch-second timestamp vectors can't share a pair within ``tol_s``.

    The timestamp-only core of :func:`series_time_ranges_disjoint` — a join
    depends solely on the two clocks, so the pruning guard does too, and the
    bucketed sweep in :func:`canlib.xanalysis.correlate_matrix` prunes on bare
    timestamp vectors (its bucket key) without materialising a series.
    """
    if not a_ts or not b_ts:
        return True
    return a_ts[0] - tol_s > b_ts[-1] or b_ts[0] - tol_s > a_ts[-1]


def series_time_ranges_disjoint(a: PreparedSeries, b: PreparedSeries, tol_s: float) -> bool:
    """True when ``a`` and ``b`` can't share a nearest pair within ``tol_s``.

    A cheap O(1) guard for the O(P²) pairwise sweeps: if the two series' time
    spans don't overlap (even after padding each end by ``tol_s``), every
    nearest-neighbour join is empty, so the whole per-pair join can be skipped.
    """
    return timestamps_disjoint(a.ts, b.ts, tol_s)


def join_indices(
    ref_ts: Sequence[float],
    cand_ts: Sequence[float],
    tol_s: float = DEFAULT_JOIN_TOL_S,
) -> tuple[list[int], list[int]]:
    """Nearest-neighbour join expressed as parallel **index** lists.

    Returns ``(ref_idx, cand_idx)``: for each kept reference sample, its position
    in ``ref_ts`` and the position of its nearest ``cand_ts`` sample within
    ``tol_s``. Reference samples with no candidate in range are dropped, so both
    lists have the realised overlap length.

    A join depends only on the two *clocks*, never on the values — so the mapping
    is reusable across every signal sharing a timestamp vector. That is what lets
    :func:`canlib.xanalysis.correlate_matrix` join once per timestamp-vector pair
    instead of once per signal pair (nearly every signal decoded from one
    ``(ECU, PID)`` shares one vector). :func:`join_prepared` is a thin wrapper over
    this, so the tie-breaking rule — smaller absolute delta wins, the earlier
    candidate on an exact tie — cannot drift between the two paths.
    """
    if not ref_ts or not cand_ts:
        return [], []
    n_cand = len(cand_ts)
    bisect_left = bisect.bisect_left
    ref_idx: list[int] = []
    cand_idx: list[int] = []
    for k, rt in enumerate(ref_ts):
        i = bisect_left(cand_ts, rt)
        best_j = -1
        best_dt = tol_s + 1.0
        for j in (i - 1, i):
            if 0 <= j < n_cand:
                delta = cand_ts[j] - rt
                if delta < 0.0:
                    delta = -delta
                if delta < best_dt:
                    best_dt = delta
                    best_j = j
        if best_j >= 0 and best_dt <= tol_s:
            ref_idx.append(k)
            cand_idx.append(best_j)
    return ref_idx, cand_idx


def join_prepared(
    ref: PreparedSeries,
    cand: PreparedSeries,
    tol_s: float = DEFAULT_JOIN_TOL_S,
) -> tuple[list[float], list[float], int]:
    """Nearest-neighbour join over :class:`PreparedSeries` (float epoch clock).

    Equivalent to :func:`join_nearest` but on pre-flattened, pre-sorted float
    arrays — no per-call sort and no ``datetime`` arithmetic in the inner loop.
    A thin wrapper over :func:`join_indices` (the single join implementation).
    """
    ref_idx, cand_idx = join_indices(ref.ts, cand.ts, tol_s)
    ref_vals = ref.values
    cand_vals = cand.values
    xs = [ref_vals[k] for k in ref_idx]
    ys = [cand_vals[j] for j in cand_idx]
    return xs, ys, len(xs)


def mirror_aligned_count(
    a: PreparedSeries,
    b: PreparedSeries,
    tol_s: float = DEFAULT_JOIN_TOL_S,
) -> int:
    """Aligned-sample count if ``a`` and ``b`` are *equal* on every aligned pair, else ``-1``.

    A fast path for mirror detection (``--find-mirrors``): it bails on the first
    value mismatch instead of building full aligned lists and comparing them
    afterwards, and never allocates the ``xs``/``ys`` lists. Returns ``-1`` as
    soon as a nearest-neighbour pair disagrees, otherwise the number of aligned
    (and equal) samples.
    """
    ref_ts = a.ts
    if not ref_ts or not b.ts:
        return 0
    cand_ts = b.ts
    cand_vals = b.values
    a_vals = a.values
    n_cand = len(cand_ts)
    bisect_left = bisect.bisect_left
    n = 0
    for k, rt in enumerate(ref_ts):
        i = bisect_left(cand_ts, rt)
        best_val: float | None = None
        best_dt = tol_s + 1.0
        for j in (i - 1, i):
            if 0 <= j < n_cand:
                delta = cand_ts[j] - rt
                if delta < 0.0:
                    delta = -delta
                if delta < best_dt:
                    best_dt = delta
                    best_val = cand_vals[j]
        if best_val is not None and best_dt <= tol_s:
            if a_vals[k] != best_val:
                return -1  # not a mirror — stop early
            n += 1
    return n


def thin_join_warning(
    *,
    command: str,
    ref_label: str,
    n_joined: int,
    n_candidates: int,
    tol_s: float,
    min_n: int | None = None,
) -> str | None:
    """Message for a reference that time-aligned onto few/none of its candidates.

    The single-reference counterpart to :func:`canlib.commands.align._warn_thin_joins`
    (which warns per *column*). ``correlate --against`` / ``hunt --against`` sweep
    many candidates against **one** reference and drop each one that fails
    ``min_n`` — so a reference whose scope simply doesn't overlap the target
    reports "nothing correlates" instead of "nothing joined", which reads like a
    real negative result rather than a tolerance/scope problem. This builds the
    warning; the caller prints it to stderr.

    ``n_joined`` is the **best** realised overlap across the sweep (the ceiling on
    any candidate), ``n_candidates`` the reference's own sample count. Returns
    ``None`` when the join is healthy.
    """
    if n_candidates == 0:
        return None
    if n_joined == 0:
        return (
            f"{command}: warning: reference {ref_label!r} joined 0 of "
            f"{n_candidates} samples onto any candidate (within \u2264{tol_s:g}s) \u2014 "
            "the scopes do not overlap in time. Widen --join-tol, or check that the "
            "reference and target were co-polled in the selected scope "
            "(`canair correlate --overlap` lists which ECU:PID pairs share samples)."
        )
    if min_n is not None and n_joined < min_n:
        return (
            f"{command}: warning: reference {ref_label!r} joined at most {n_joined} "
            f"sample(s) onto any candidate (within \u2264{tol_s:g}s), below --min-n "
            f"{min_n} \u2014 every candidate was dropped for thin overlap, not for a weak "
            "correlation. Widen --join-tol or lower --min-n."
        )
    floor = max(1, n_candidates // 20)  # 5% — matches align's per-column floor
    if n_joined < floor:
        return (
            f"{command}: warning: reference {ref_label!r} joined only {n_joined} of "
            f"{n_candidates} samples onto its best candidate (within \u2264{tol_s:g}s) "
            "\u2014 consider a larger --join-tol; thin overlap makes |r| unstable."
        )
    return None


def join_nearest_triple(
    ref: list[TimePoint],
    cand: list[TimePoint],
    control: list[TimePoint],
    tol_s: float = DEFAULT_JOIN_TOL_S,
) -> tuple[list[float], list[float], list[float], int]:
    """Three-way nearest join: keep points where ``ref`` has a nearest ``cand``
    **and** a nearest ``control`` within ``tol_s``.

    Returns ``(ref_vals, cand_vals, control_vals, n)`` aligned triples — the input
    a partial correlation needs (reference, candidate, and the nuisance signal
    regressed out, all sampled at the same reference instants). Reference points
    missing either neighbour are dropped, and the realised ``n`` is reported so a
    thin three-way overlap is visible.
    """
    _ref_vals, cols = align_many(ref, {"c": cand, "z": control}, tol_s)
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    ref_sorted = sorted(ref, key=lambda tp: tp.dt)
    for rp, cy, cz in zip(ref_sorted, cols["c"], cols["z"], strict=True):
        if cy is not None and cz is not None:
            xs.append(rp.value)
            ys.append(cy)
            zs.append(cz)
    return xs, ys, zs, len(xs)


def align_many(
    reference: list[TimePoint],
    others: dict[str, list[TimePoint]],
    tol_s: float = DEFAULT_JOIN_TOL_S,
) -> tuple[list[float], dict[str, list[float | None]]]:
    """Align every series in ``others`` onto ``reference`` by nearest timestamp.

    Returns ``(ref_values, columns)`` where ``ref_values`` is the reference
    series' values (in time order) and ``columns[name]`` is a same-length list of
    the nearest ``other`` value within ``tol_s`` (or ``None`` when out of range).

    Unlike :func:`join_nearest` this keeps *every* reference row (padding with
    ``None``) so a caller can build a rectangular table for a correlation matrix
    and decide per-pair how to drop the gaps.
    """
    ref_sorted = sorted(reference, key=lambda tp: tp.dt)
    ref_vals = [tp.value for tp in ref_sorted]
    columns: dict[str, list[float | None]] = {}
    for name, series in others.items():
        cand_sorted = sorted(series, key=lambda tp: tp.dt)
        cand_ts = [tp.dt for tp in cand_sorted]
        col: list[float | None] = []
        for rp in ref_sorted:
            best_val: float | None = None
            best_dt = tol_s + 1.0
            if cand_ts:
                i = bisect.bisect_left(cand_ts, rp.dt)
                for j in (i - 1, i):
                    if 0 <= j < len(cand_sorted):
                        delta = abs((cand_ts[j] - rp.dt).total_seconds())
                        if delta < best_dt:
                            best_dt = delta
                            best_val = cand_sorted[j].value
            col.append(best_val if best_dt <= tol_s else None)
        columns[name] = col
    return ref_vals, columns
