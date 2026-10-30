"""Row-aligned ISO-TP payload matrix shared by the whole-history ``investigate`` views.

``--counters`` and ``--support-map`` both reason over many captures of one PID at once, so both
need the payloads cut to a common length. ISO-TP space (SID first, no PCI) is used because a
multi-byte field must be genuinely contiguous there.
"""

from __future__ import annotations

from datetime import datetime

from canlib.analysis.align import LoadedPid

# Fraction of captures that must reach the common prefix length (see payload_matrix). Below
# this, a payload is treated as too short to align.
MIN_LEN_FRAC = 0.9


def payload_matrix(lp: LoadedPid) -> tuple[list[datetime], list[bytes]]:
    """Time-ordered ISO-TP payloads truncated to a common prefix length.

    Aligned on a common **prefix**, not the modal length. An ISO-TP response is
    frequently padded (a trailing 0xAA), so the same field sits at the same offset
    in a 12- and a 13-byte payload; filtering to the modal length discarded 478 of
    BCM 22C011's 3524 captures — three months of the horizon a long-run counter
    search depends on. Takes the longest prefix that :data:`MIN_LEN_FRAC` of
    captures reach and keeps every capture reaching it.
    """
    rows = [(d.dt, d.payload_bytes) for d in lp.decoded if d.dt is not None]
    rows.sort(key=lambda r: r[0])
    if not rows:
        return [], []
    lens = sorted(len(p) for _dt, p in rows)
    usable = lens[max(0, int(len(lens) * (1.0 - MIN_LEN_FRAC)))]
    kept = [(dt, p[:usable]) for dt, p in rows if len(p) >= usable]
    if not kept or usable < 1:
        return [], []
    return [dt for dt, _p in kept], [p for _p_dt, p in kept]
