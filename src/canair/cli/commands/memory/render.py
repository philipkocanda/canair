"""Text and JSON for the memory commands: a scan's region map, a dump, a diff, a hex dump."""

from __future__ import annotations

import json
import sys
from typing import Any

from canair.ecus.memory import MemoryRegion
from canair.live.memory.dump import DumpRun
from canair.memory.diff import DumpDiff
from canair.memory.store import Dump
from canair.render import ansi, messages
from canair.render.formatting import duration_text, rate_text, size_text
from canair.render.glyphs import OK
from canair.render.paint import Painter, column_widths, render_row

#: A dump header lists this many ranges, then says how many more there are.
MAX_RANGES_SHOWN = 8


def span_text(start: int, end: int, width: int) -> str:
    return f"0x{start:0{width}X}-0x{end:0{width}X}"


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


def address_width(end: int) -> int:
    """Hex digits to print addresses up to ``end`` with: 6 for a 3-byte space, 8 beyond."""
    return 2 * max(3, (len(f"{end:X}") + 1) // 2)


def dump_header_lines(
    ecu: str,
    tx_id: int,
    ranges: list[tuple[int, int]],
    block: int,
    transport: str | None,
    paint: Painter,
) -> list[str]:
    """What a dump is about to read: the ECU, how, and each range with its size."""
    width = address_width(max(end for _, end in ranges))
    how = ["memory dump", f"block {block}", *([f"over {transport}"] if transport else [])]
    name = paint(f"{ecu} (0x{tx_id:03X})", ansi.raw.BOLD + ansi.raw.CYAN)
    lines = [f"\n  {name}{paint(' · ' + ' · '.join(how), ansi.raw.DIM)}"]
    shown = ranges[:MAX_RANGES_SHOWN]
    rows = [
        [
            (f"{i}" if len(ranges) > 1 else "", ansi.raw.DIM),
            (span_text(start, end, width), ""),
            (size_text(end - start + 1).rjust(10), ""),
        ]
        for i, (start, end) in enumerate(shown, 1)
    ]
    widths = column_widths(rows)
    lines += [render_row(row, widths, paint) for row in rows]
    if len(shown) < len(ranges):
        lines.append(paint(f"    … and {len(ranges) - len(shown)} more", ansi.raw.DIM))
    total = size_text(sum(end - start + 1 for start, end in ranges))
    count = f"{len(ranges)} ranges · " if len(ranges) > 1 else ""
    lines.append(paint(f"  {count}{total}", ansi.raw.DIM))
    return lines


def print_dump_header(
    ecu: str, tx_id: int, ranges: list[tuple[int, int]], block: int, transport: str | None
) -> None:
    """The header of a dump, on stderr (the result stays on stdout)."""
    paint = Painter(sys.stderr)
    print(
        "\n".join(dump_header_lines(ecu, tx_id, ranges, block, transport, paint)), file=sys.stderr
    )


def run_doc(run: DumpRun) -> dict[str, Any]:
    return {
        "seconds": round(run.seconds, 1),
        "bytes_per_second": round(run_rate(run), 1) if run.seconds > 0 else None,
        "retries": run.retries,
        "halvings": run.halvings + run.drops,
        "rereads": run.rereads,
        "stopped": run.stopped,
    }


def run_rate(run: DumpRun) -> float:
    return run.dump.bytes_read / run.seconds if run.seconds > 0 else 0.0


def run_lines(run: DumpRun, paint: Painter) -> list[str]:
    """How the read went: the time and speed, then whatever the link cost (nothing if clean)."""
    lines = []
    if run.seconds > 0:
        speed = f" ({rate_text(run_rate(run))})" if run.dump.bytes_read else ""
        lines.append(paint(f"  this run: {duration_text(run.seconds)}{speed}", ansi.raw.DIM))
    costs = [
        (run.retries, "lost replies read again"),
        (run.halvings + run.drops, "blocks halved"),
        (run.rereads, "stale replies re-read"),
    ]
    parts = [f"{n} {words}" for n, words in costs if n]
    if parts:
        lines.append(paint("  link: " + " · ".join(parts), ansi.raw.YELLOW))
    return lines


def _result_cell(result: str, nrc: int | None) -> tuple[str, str]:
    if result == "read":
        return result, ansi.raw.GREEN
    return (f"{result}  NRC 0x{nrc:02X}" if nrc is not None else result), ansi.raw.YELLOW


def print_dump_summary(
    dump: Dump, *, as_json: bool, note: str | None = None, run: DumpRun | None = None
) -> None:
    if as_json:
        doc = dump_doc(dump)
        if run is not None:
            doc["run"] = run_doc(run)
        print(json.dumps(doc, indent=2))
        return
    paint = Painter(sys.stdout)
    width = address_width(max((s.end for s in dump.stretches), default=0))
    details = [dump.ecu, f"{size_text(dump.bytes_read)} read"]
    if dump.software:
        details.append(f"software {dump.software}")
    print(paint.heading(dump.id, *details))
    rows = [
        [
            (span_text(s.start, s.end, width), ""),
            (size_text(len(s.data)).rjust(10), ""),
            _result_cell("read", None),
        ]
        for s in dump.stretches
    ]
    rows += [
        [
            (span_text(g.start, g.end, width), ""),
            (size_text(g.end - g.start + 1).rjust(10), ""),
            _result_cell(g.result, g.nrc),
        ]
        for g in dump.gaps
    ]
    widths = column_widths(rows)
    for row in rows:
        print(render_row(row, widths, paint))
    if run is not None:
        for line in run_lines(run, paint):
            print(line)
    if note:
        messages.warning(note)
    if dump.path is not None:
        tick = Painter(sys.stderr)(OK, ansi.raw.GREEN)
        print(f"  {tick} Saved to {dump.path} (local only; never shared)", file=sys.stderr)


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
