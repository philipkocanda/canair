"""What ``investigate`` reports about one byte, and how it scores it.

The per-byte record and the scoring that ranks it: state-discriminability, the
strongest co-polled cross-signal anchor (with its linear fit and unit guess), the
independence weighting behind ``--independent-of``, and the multi-byte word
heuristic. Pure — no printing, no capture loading — so each score is testable in
isolation from the sweep that produces it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from canlib.align import (
    PreparedSeries,
    TimePoint,
    join_prepared,
    prepare_series,
)
from canlib.triage import WordCandidate
from canlib.xanalysis import (
    correlation,
    linear_fit,
    sniff_unit,
)

NAME = "investigate"


@dataclass
class _ByteReport:
    offset: int
    mapped_by: str | None
    mapped_verified: bool
    state_f: float | None
    anchor: str | None
    anchor_r: float | None
    anchor_n: int
    slope: float | None
    intercept: float | None
    unit_guess: str | None
    bit: int | None = None  # None = whole byte; 0-7 = a single bit Bn:k
    driver_r: float | None = None  # |r| vs the --independent-of driver, if set
    independence: float | None = None  # state_f weighted by (1 - |driver_r|)
    physical: str | None = None  # named physical band this byte's value lands in
    kind: str | None = None  # triage class: constant/counter/checksum/enum/continuous
    entropy: float | None = None  # Shannon entropy (bits) of the byte's values
    lag1: float | None = None  # lag-1 (sample) autocorrelation

    @property
    def label(self) -> str:
        return f"B{self.offset}:{self.bit}" if self.bit is not None else f"B{self.offset}"


def _independence_score(state_f: float | None, driver_r: float | None) -> float | None:
    """State separation weighted by independence from the driver.

    High when a byte separates cleanly by state (large ``state_f``) *and* barely
    tracks the driver (small ``|driver_r|``) — the "active but independent"
    fingerprint. ``None`` when there is no state separation to rank by. A missing
    ``driver_r`` (no time overlap with the driver) counts as fully independent.
    """
    if state_f is None:
        return None
    f = 1e6 if state_f == float("inf") else state_f
    return f * (1.0 - min(1.0, abs(driver_r or 0.0)))


def _state_f(frames_by_state: dict[str, list[float]]):
    from canlib.xanalysis import discriminability

    return discriminability(frames_by_state)


def _parse_bit_key(key: str) -> tuple[int, int]:
    """``ECU:PID:B10:5`` → ``(10, 5)`` — the last two colon fields are offset:bit."""
    _, off, bit = key.rsplit(":", 2)
    return int(off.lstrip("B")), int(bit)


class AnchorHit(NamedTuple):
    label: str  # the anchoring ECU:PID:PARAM signal
    r: float  # Pearson correlation with the target byte
    n: int  # aligned sample count
    slope: float | None  # least-squares fit y = slope·x + intercept
    intercept: float | None
    unit_guess: str | None  # sniffed physical-unit guess for the fit


def _best_anchor(
    series: list[TimePoint],
    anchors_prepared: dict[str, PreparedSeries],
    tol: float,
    min_n: int,
) -> AnchorHit | None:
    """The strongest-correlating anchor for one byte series.

    ``anchors_prepared`` maps label → :class:`PreparedSeries` (built once by the
    caller); the target ``series`` is prepared once here and joined against each
    anchor, so the same anchor is never re-sorted/re-flattened per target byte.
    """
    if not anchors_prepared:
        return None
    ps = prepare_series(series)
    best: AnchorHit | None = None
    for label, aprep in anchors_prepared.items():
        xs, ys, n = join_prepared(aprep, ps, tol_s=tol)
        if n < min_n:
            continue
        r = correlation(xs, ys)
        if r is None:
            continue
        if best is None or abs(r) > abs(best.r):
            fit = linear_fit(xs, ys)
            m, c = (fit[0], fit[1]) if fit else (None, None)
            best = AnchorHit(label, r, n, m, c, sniff_unit(xs, ys))
    return best


def _driver_r(
    series: list[TimePoint],
    driver_prepared: PreparedSeries | None,
    tol: float,
    min_n: int,
) -> float | None:
    """Correlation of one byte/bit series vs the --independent-of driver, or None.

    ``driver_prepared`` is a :class:`PreparedSeries` (built once) or ``None``.
    """
    if driver_prepared is None:
        return None
    xs, ys, n = join_prepared(driver_prepared, prepare_series(series), tol_s=tol)
    if n < min_n:
        return None
    return correlation(xs, ys)


def _word_expr(word: WordCandidate) -> str | None:
    """WiCAN big-endian word expression for a detected (hi, lo) offset pair.

    Returns ``None`` when the pair is **not** ISO-TP-adjacent (a gap byte sits
    between them) — such a pair isn't a real 16-bit word and must not be rendered
    as a misleading ``[Bhi:Blo]`` range. Adjacent pairs (including PCI-straddling
    ones) render via :class:`ByteRef`, which emits the correct shift-composition.
    """
    from canlib.byteindex import wican_to_isotp
    from canlib.notation import ByteRef

    hi_key, lo_key = word.hi_key, word.lo_key
    if not (isinstance(hi_key, int) and isinstance(lo_key, int)):
        return None  # only int byte-offset keys form a UDS word expression
    hi_iso = wican_to_isotp(hi_key)
    lo_iso = wican_to_isotp(lo_key)
    if hi_iso is None or lo_iso is None or lo_iso != hi_iso + 1:
        return None
    return ByteRef.from_isotp(hi_iso, width=2).to_wican_expression()
