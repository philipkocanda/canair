"""Text and JSON for the memory commands: a scan's region map, a dump, a diff, a hex dump."""

from __future__ import annotations

import json
import sys
from typing import Any

from canair.ecus.memory import MemoryRegion
from canair.memory.diff import DumpDiff
from canair.memory.store import Dump
from canair.render import messages


def span_text(start: int, end: int, width: int) -> str:
    return f"0x{start:0{width}X}-0x{end:0{width}X}"


def size_text(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    return f"{n / 1024:.1f} KiB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MiB"


def region_rows(regions: list[MemoryRegion]) -> list[dict[str, Any]]:
    return [
        {
            "start": r.start,
            "end": r.end,
            "result": r.result,
            "nrc": r.nrc,
            "software": r.software,
        }
        for r in regions
    ]


def print_scan(scan: Any, *, as_json: bool) -> None:
    """``scan memory``'s result (a :class:`canair.live.memory.scan.MemoryScan`)."""
    width = 2 * scan.format.address_bytes if scan.format is not None else 8
    if as_json:
        doc = {
            "ecu": scan.ecu,
            "tx_id": scan.tx_id,
            "format": scan.format.name if scan.format is not None else None,
            "software": scan.software,
            "max_block": scan.max_block,
            "ranges": [{"start": a, "end": b} for a, b in scan.ranges],
            "reads": scan.reads,
            "stopped": scan.stopped,
            "format_probes": [
                {"format": p.format, "result": str(p.result), "nrc": p.nrc}
                for p in scan.format_probes
            ],
            "regions": region_rows(scan.regions),
            "written": str(scan.written) if scan.written else None,
        }
        print(json.dumps(doc, indent=2))
        return
    fmt = scan.format.name if scan.format is not None else "unknown"
    print(f"\n  {scan.ecu} (0x{scan.tx_id:03X}) · memory format {fmt}", end="")
    print(f" · software {scan.software}" if scan.software else "")
    for p in scan.format_probes:
        nrc = f" (NRC 0x{p.nrc:02X})" if p.nrc is not None else ""
        print(f"    format {p.format}: {p.result}{nrc}")
    if scan.regions:
        print()
        for r in scan.regions:
            nrc = f"  NRC 0x{r.nrc:02X}" if r.nrc is not None else ""
            size = size_text(r.end - r.start + 1)
            print(f"    {span_text(r.start, r.end, width)}  {size:>10}  {r.result}{nrc}")
    if scan.max_block:
        print(f"\n  Largest block that came back whole: {scan.max_block} bytes")
    print(f"  {scan.reads} read(s)")
    if scan.stopped:
        messages.warning(f"scan stopped: {scan.stopped}")
    if scan.written:
        print(f"  ✓ Recorded the map in {scan.written}", file=sys.stderr)


def dump_doc(dump: Dump) -> dict[str, Any]:
    return {
        "id": dump.id,
        "path": str(dump.path) if dump.path else None,
        "ecu": dump.ecu,
        "tx_id": dump.tx_id,
        "format": dump.memory_format,
        "software": dump.software,
        "taken": dump.taken,
        "label": dump.label,
        "complete": dump.complete,
        "resumable": dump.resumable,
        "bytes_read": dump.bytes_read,
        "stretches": [{"start": s.start, "end": s.end} for s in dump.stretches],
        "gaps": [
            {"start": g.start, "end": g.end, "result": g.result, "nrc": g.nrc} for g in dump.gaps
        ],
    }


def print_dump_summary(dump: Dump, *, as_json: bool, note: str | None = None) -> None:
    if as_json:
        print(json.dumps(dump_doc(dump), indent=2))
        return
    width = 2 * max(4, len(f"{max((s.end for s in dump.stretches), default=0):X}") // 2)
    print(f"\n  {dump.id}  ·  {dump.ecu} · {size_text(dump.bytes_read)} read", end="")
    print(f" · software {dump.software}" if dump.software else "")
    for s in dump.stretches:
        print(f"    {span_text(s.start, s.end, width)}  {size_text(len(s.data)):>10}  read")
    for g in dump.gaps:
        nrc = f"  NRC 0x{g.nrc:02X}" if g.nrc is not None else ""
        print(
            f"    {span_text(g.start, g.end, width)}  {size_text(g.end - g.start + 1):>10}  {g.result}{nrc}"
        )
    if note:
        messages.warning(note)
    if dump.path is not None:
        print(f"  ✓ Saved to {dump.path} (local only; never shared)", file=sys.stderr)


def print_listing(dumps: list[Dump], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([dump_doc(d) for d in dumps], indent=2))
        return
    if not dumps:
        print("  No dumps yet (make one with `canair memory dump`).")
        return
    for d in dumps:
        state = "  (resumable)" if d.resumable else "" if d.complete else "  (incomplete)"
        label = f"  {d.label}" if d.label else ""
        print(f"  {d.id:<40} {size_text(d.bytes_read):>10}  {d.software or '-'}{label}{state}")


def print_hexdump(dump: Dump, start: int | None, end: int | None, *, as_json: bool) -> None:
    rows = []
    for s in dump.stretches:
        lo = max(s.start, start if start is not None else s.start)
        hi = min(s.end, end if end is not None else s.end)
        if lo > hi:
            continue
        rows.append((lo, s.data[lo - s.start : hi - s.start + 1]))
    if as_json:
        print(json.dumps([{"start": a, "hex": b.hex().upper()} for a, b in rows], indent=2))
        return
    for base, data in rows:
        for i in range(0, len(data), 16):
            chunk = data[i : i + 16]
            text = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
            print(f"  {base + i:08X}  {chunk.hex(' ').upper():<47}  {text}")


def print_diff(diff: DumpDiff, *, as_json: bool) -> None:
    if as_json:
        doc = {
            "compared": diff.compared,
            "only_a": diff.only_a,
            "only_b": diff.only_b,
            "runs": [
                {"start": r.start, "end": r.end, "a": r.a.hex().upper(), "b": r.b.hex().upper()}
                for r in diff.runs
            ],
        }
        print(json.dumps(doc, indent=2))
        return
    print(f"\n  {len(diff.runs)} changed run(s) in {diff.compared} bytes both dumps read")
    if diff.only_a or diff.only_b:
        print(f"  ({diff.only_a} bytes only in the first, {diff.only_b} only in the second)")
    for r in diff.runs:
        a, b = r.a.hex(" ").upper(), r.b.hex(" ").upper()
        print(f"    0x{r.start:08X}  +{len(r.a):<4} {a}  →  {b}")
