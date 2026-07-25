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
    """Parse a comma-separated arbitration-ID filter (``0x220,0x386``) to ints."""
    if not spec:
        return None
    out: set[int] = set()
    for tok in spec.split(","):
        tok = tok.strip()
        if tok:
            out.add(int(tok, 16))  # hex arbitration ID, with or without 0x prefix
    return out or None
