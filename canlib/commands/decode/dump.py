"""``--dump-bytes``: the timestamp x byte-offset matrix, as CSV or JSON.

Deliberately not a rendered table — it is the structured escape hatch for ad-hoc
byte analysis, so it emits machine formats with no ANSI and no signal decoding.
Timestamps are harmonised with ``align``/``decode --json``: CSV carries an
absolute ``YYYY-MM-DD HH:MM:SS.ffffff``, JSON a time-only ``time`` plus a separate
``date``, so a dump CSV and an ``align --csv`` join without reformatting.
"""

from __future__ import annotations

import csv
import json
import sys

from canlib.byteindex import wican_to_isotp
from canlib.notation import ByteNotation


def _entry_dt(cap: dict):
    """Real datetime for a capture (session date + per-capture time), or None."""
    from canlib.capture_dates import entry_datetime

    return entry_datetime(cap)


def _dump_column_label(
    off: int, include_pci: bool, notation: ByteNotation, sub_bytes: int, *, signed: bool = False
) -> str:
    """Column label for WiCAN offset ``off`` in the byte-matrix export."""
    from canlib.notation import ByteRef

    if wican_to_isotp(off) is None:
        return f"B{off}"  # PCI framing byte — no ISO-TP position, WiCAN label only
    try:
        return ByteRef.from_wican(off, signed=signed).render(notation, sub_bytes=sub_bytes)
    except ValueError:
        return f"{'S' if signed else 'B'}{off}"


def _dump_cell(fr: bytes, off: int, *, signed: bool) -> int | None:
    """Byte value at ``off``, reinterpreted as signed (-128..127) when requested.

    PCI framing bytes stay unsigned — signedness is only meaningful for a data
    byte an analyst might read as the high half of a signed value.
    """

    if off >= len(fr):
        return None
    v = fr[off]
    if signed and wican_to_isotp(off) is not None and v >= 128:
        return v - 256
    return v


def _dump_bytes(
    all_results: list[dict],
    ecu_key: str,
    pid_key: str,
    *,
    as_json: bool,
    include_pci: bool,
    notation: ByteNotation,
    sub_bytes: int,
    signed: bool = False,
) -> int:
    """Emit a ``timestamp × byte-offset`` matrix, one row per capture.

    The first-class, structured replacement for regex-scraping ``captures --diff``
    text: dump the raw byte values of every scoped capture as CSV (default) or
    JSON for ad-hoc analysis. Columns are WiCAN ``Bnn`` (relabelled by
    ``--notation``); ISO-TP PCI framing bytes are skipped unless ``include_pci``.
    A capture shorter than the widest frame leaves trailing cells blank/null.

    With ``signed``, each data byte is reinterpreted as a two's-complement value
    (-128..127) under an ``Snn`` header — so a byte that is the high half of a
    signed quantity (a ``0xFF`` near-zero baseline) reads as a small negative
    value and correlates cleanly, instead of the unsigned ``255`` foot-gun.
    """
    from canlib.byteindex import payload_to_wican_bytes

    rows: list[tuple[dict, bytes]] = []
    max_len = 0
    for r in all_results:
        cap = r["capture"]
        try:
            fr = payload_to_wican_bytes(cap["payload"])
        except Exception:
            fr = b""
        rows.append((cap, fr))
        max_len = max(max_len, len(fr))

    offsets = [off for off in range(max_len) if include_pci or wican_to_isotp(off) is not None]
    labels = [
        _dump_column_label(off, include_pci, notation, sub_bytes, signed=signed) for off in offsets
    ]

    def _row_time(cap: dict, *, full: bool) -> str:
        """Timestamp for a row, harmonized with ``decode``/``align`` output.

        ``full`` (CSV) → absolute ``YYYY-MM-DD HH:MM:SS.ffffff`` (joinable with an
        ``align --csv`` dump); otherwise (JSON) → time-only ``HH:MM:SS.ffffff`` to
        match the ``decode --json`` / ``align --json`` shape (which carry ``date``
        as a separate field). Neither uses the ISO ``T`` separator, so the two
        pulls join without reformatting.
        """
        dt = _entry_dt(cap)
        if dt is not None:
            return dt.strftime("%Y-%m-%d %H:%M:%S.%f") if full else dt.strftime("%H:%M:%S.%f")
        if full:
            return f"{cap.get('date', '')} {cap.get('time', '')}".strip()
        return str(cap.get("time", ""))

    if as_json:
        out = {
            "ecu": ecu_key,
            "pid": pid_key,
            "notation": notation.value,
            "include_pci": include_pci,
            "columns": labels,
            "offsets": offsets,
            "rows": [
                {
                    "time": _row_time(cap, full=False),
                    "date": str(cap.get("date", "")),
                    "vehicle_states": cap.get("vehicle_states") or [],
                    "bytes": {
                        lbl: _dump_cell(fr, off, signed=signed)
                        for lbl, off in zip(labels, offsets, strict=True)
                    },
                }
                for cap, fr in rows
            ],
        }
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        return 0

    writer = csv.writer(sys.stdout)
    writer.writerow(["time", "ecu", "pid", *labels])
    for cap, fr in rows:
        writer.writerow(
            [
                _row_time(cap, full=True),
                ecu_key,
                pid_key,
                *[
                    (v if (v := _dump_cell(fr, off, signed=signed)) is not None else "")
                    for off in offsets
                ],
            ]
        )
    return 0
