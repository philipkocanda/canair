"""Time-series extraction from raw broadcast-CAN frame logs (domain B).

The frame-domain analogue of :func:`canlib.xanalysis.build_byte_series`: turn a
native CAN frame log (``captures/can/``) into per-byte (and per-bit) time series
keyed by ``arbitration-ID:byte``, so broadcast data flows into the *same*
``TimePoint``-based analysis core (``align``/``xanalysis`` — correlate/hunt) as
diagnostic captures, with no re-implementation of the analyzer.

Frames have **no ISO-TP framing and no PCI**, so a byte is referenced directly by
its offset in the frame data — rendered ``0xID:rN`` (raw-CAN space, see
:class:`canlib.notation.ByteRef`), distinct from the WiCAN ``Bn`` used for
diagnostic PIDs. WiCAN expressions and the diagnostic path are untouched.
"""

from __future__ import annotations

from datetime import datetime

from .align import TimePoint
from .can_logs import detect_format, iter_frames

# Frame-signal label: "0xID:rN" (byte) / "0xID:rN.k" (bit). The single ":" keeps
# arbitration-ID grouping working in xanalysis._same_pid (which rsplits on ":").


def _id_label(arb_id: int) -> str:
    return f"0x{arb_id:X}"


def build_frame_series(
    path,
    fmt: str | None = None,
    *,
    min_distinct: int = 4,
    id_filter: set[int] | None = None,
) -> dict[str, list[TimePoint]]:
    """One time series per varying data byte across a frame log.

    Reads the log once, grouping ``data[k]`` for each ``(arbitration_id, k)`` into
    a series labelled ``0xID:rk``. Near-constant bytes (``< min_distinct`` distinct
    values) are dropped — they can't correlate. ``id_filter`` restricts to a set
    of arbitration IDs.
    """
    from pathlib import Path

    p = Path(path)
    resolved = detect_format(p, fmt)
    series: dict[str, list[TimePoint]] = {}
    for msg in iter_frames(p, resolved):
        if id_filter is not None and msg.arbitration_id not in id_filter:
            continue
        dt = datetime.fromtimestamp(msg.timestamp) if msg.timestamp else None
        if dt is None:
            continue
        idl = _id_label(msg.arbitration_id)
        for k, byte in enumerate(msg.data):
            series.setdefault(f"{idl}:r{k}", []).append(TimePoint(dt, float(byte)))
    return {
        name: pts for name, pts in series.items() if len({p.value for p in pts}) >= min_distinct
    }


def build_frame_bit_series(
    path,
    fmt: str | None = None,
    *,
    id_filter: set[int] | None = None,
) -> dict[str, list[TimePoint]]:
    """One 0/1 series per toggling data bit across a frame log (``0xID:rk.b``).

    The bit-level companion to :func:`build_frame_series`; only bits with ≥2
    distinct values are kept.
    """
    from pathlib import Path

    p = Path(path)
    resolved = detect_format(p, fmt)
    series: dict[str, list[TimePoint]] = {}
    for msg in iter_frames(p, resolved):
        if id_filter is not None and msg.arbitration_id not in id_filter:
            continue
        dt = datetime.fromtimestamp(msg.timestamp) if msg.timestamp else None
        if dt is None:
            continue
        idl = _id_label(msg.arbitration_id)
        for k, byte in enumerate(msg.data):
            for b in range(8):
                series.setdefault(f"{idl}:r{k}.{b}", []).append(
                    TimePoint(dt, float((byte >> b) & 1))
                )
    return {name: pts for name, pts in series.items() if len({p.value for p in pts}) >= 2}


def parse_id_filter(spec: str | None) -> set[int] | None:
    """Parse a comma-separated arbitration-ID filter (``0x220,0x386``) to ints.

    Raises ``ValueError`` with a user-facing message on a non-hex token (rather
    than leaking Python's ``invalid literal for int()``).
    """
    if not spec:
        return None
    out: set[int] = set()
    for tok in spec.split(","):
        tok = tok.strip()
        if tok:
            try:
                out.add(int(tok, 16))  # hex arbitration ID, with or without 0x prefix
            except ValueError:
                raise ValueError(
                    f"invalid arbitration ID {tok!r} — expected hex like 0x386 or 386"
                ) from None
    return out or None


def _frame_expr(off: int, spec: tuple, little: bool) -> str | None:
    """Raw-CAN label for an interpretation of a frame at ``off``.

    Frames have no ISO-TP/PCI and no WiCAN expression language: a byte is ``rN``,
    a big-endian multi-byte read a ``[rN:rM]`` range, and a little-endian *unsigned*
    read a shift-composition (``r0 | (r1 << 8)``) — mirroring the diagnostic
    ``wican_expr``. Only floats and little-endian *signed* reads have no simple raw
    label (``None`` → ranked/demoted as the diagnostic hunt's ``<no-expr>``).
    Definitions ultimately live in the linear ``signals/`` model (Stage 4), not an
    expression — this is a display label for analysis.
    """
    _, width, kind, signed = spec
    if kind == "float":
        return None
    if width == 1:
        return f"r{off}"
    if not little:
        return f"[r{off}:r{off + width - 1}]"
    if signed:
        return None
    terms = [f"r{off}"] + [f"(r{off + k} << {8 * k})" for k in range(1, width)]
    return " | ".join(terms)


def hunt_frame(
    path,
    fmt: str | None,
    target_id: int,
    ref: list[TimePoint],
    *,
    tol_s: float,
    min_n: int = 10,
    top: int = 12,
    method: str = "pearson",
    all_interps: bool = False,
):
    """ "Which byte/interpretation of frame ``target_id`` *is* ``ref``?"

    The frame-domain analogue of :func:`canlib.xanalysis.hunt_byte`: sweeps every
    byte offset × interpretation (u8/i16/f32/… × endianness) of one arbitration
    ID's frames, time-aligns each against ``ref``, and ranks by |r| — reusing the
    plot inspector's ``INSPECT_TYPES``/``interpret_bytes`` and the shared
    ranking/collapse. No PCI to skip (frames are contiguous). Returns
    :class:`~canlib.xanalysis.HuntHit` with raw-CAN ``rN`` labels.
    """
    from pathlib import Path

    from .align import join_nearest
    from .commands._decode_plot import INSPECT_TYPES, interpret_bytes
    from .xanalysis import HuntHit, _rank_and_collapse, correlation, linear_fit, sniff_unit

    p = Path(path)
    resolved = detect_format(p, fmt)
    frames: list[tuple[datetime, bytes]] = []
    max_len = 0
    for msg in iter_frames(p, resolved):
        if msg.arbitration_id != target_id or not msg.timestamp:
            continue
        frames.append((datetime.fromtimestamp(msg.timestamp), bytes(msg.data)))
        max_len = max(max_len, len(msg.data))
    if not frames:
        return []

    hits: list[HuntHit] = []
    for spec in INSPECT_TYPES:
        _, width, _kind, _signed = spec
        for little in (False, True) if width > 1 else (False,):
            for off in range(max_len):
                if off + width > max_len:
                    continue
                cand: list[TimePoint] = []
                for dt, data in frames:
                    v = interpret_bytes(data, off, spec, little=little)
                    if v is not None:
                        cand.append(TimePoint(dt, v))
                if len({tp.value for tp in cand}) < 3:
                    continue
                xs, ys, n = join_nearest(ref, cand, tol_s=tol_s)
                if n < min_n:
                    continue
                r = correlation(xs, ys, method)
                if r is None:
                    continue
                fit = linear_fit(xs, ys)
                if fit is None:
                    continue
                m, c, resid = fit
                hits.append(
                    HuntHit(
                        expr=_frame_expr(off, spec, little) or "<no-expr>",
                        interp=spec[0] + (" LE" if little and width > 1 else ""),
                        offset=off,
                        r=r,
                        n=n,
                        slope=m,
                        intercept=c,
                        resid=resid,
                        unit_guess=sniff_unit(xs, ys),
                        width=width,
                    )
                )
    return _rank_and_collapse(hits, top=top, all_interps=all_interps)
