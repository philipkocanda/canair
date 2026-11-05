"""``investigate --support-map`` for field maps: one bitmap bit per parameter of a record.

Bridges :mod:`canair.analysis.support_fields` to a scanned PID: turns the defined signals into
field extents in data space, tries the spec's MSB-first order and then LSB-first, and renders
the split. Kept apart from :mod:`.support_map` (the per-bit/per-byte bridge), which calls in
here once per PID.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING

from canair.analysis.support_fields import FieldFit, known_fields, solve_fields
from canair.analysis.support_map import MIN_ROWS, MODAL_FRAC
from canair.render import ansi
from canair.uds.byteindex import extract_byte_indices
from canair.uds.notation import ByteNotation, relabel_signal, subfunction_bytes_for_pid
from canair.uds.support_marks import MAX_MAP_LEN

if TYPE_CHECKING:
    from .support_map import PidScan

# A fit whose defined signals read less than this share of the covered bytes says little: the
# undefined bytes can be grouped almost any way.
MIN_CLAIMED_FRAC = 0.5


def offset_label(
    labels: dict[int, int], header: int, pid: str, payload_len: int, off: int, n: ByteNotation
) -> str:
    """A data offset in the chosen byte notation (``B28`` in WiCAN)."""
    w = labels.get(header + off)
    text = f"B{w}" if w is not None else f"i{header + off}"
    return relabel_signal(
        text, n, sub_bytes=subfunction_bytes_for_pid(pid), payload_len=payload_len or None
    )


def _known(scan: PidScan) -> list[tuple[int, int]]:
    """Field extents (data offsets) the PID's defined signals imply."""
    to_index = {w: p for p, w in scan.labels.items()}
    sets = []
    for pdef in scan.params.values():
        refs = extract_byte_indices(pdef.get("expression") or "")
        sets.append({to_index[w] - scan.header for w in refs if w in to_index})
    return known_fields(s for s in sets if s)


def fit_fields(scan: PidScan, rows: Sequence[bytes], max_at: int) -> FieldFit | None:
    """The field reading of ``rows`` (data bytes), or None when they hold no candidate bitmap.

    A candidate is a constant, non-zero 4-byte lead with more than four bytes after it (four or
    fewer is the bitmapped layout the per-bit reading owns). MSB first is the spec's order and
    is kept whenever it fits; LSB first is reported only where MSB first is broken.
    """
    if len(rows) < MIN_ROWS:
        return None
    length = MAX_MAP_LEN
    width = min(len(r) for r in rows)
    for at in range(max_at + 1):
        if width - at - length <= length:
            return None
        lead, count = Counter(r[at : at + length] for r in rows).most_common(1)[0]
        if count >= MODAL_FRAC * len(rows) and any(lead):
            break
    else:
        return None
    use = [r for r in rows if r[at : at + length] == lead]
    known = _known(scan)
    msb = solve_fields(use, known, at=at, length=length, order="msb")
    if msb is None or msb.verdict != "broken":
        return msb
    lsb = solve_fields(use, known, at=at, length=length, order="lsb")
    return lsb if lsb is not None and lsb.verdict != "broken" else msb


def thin(fit: FieldFit) -> bool:
    """Too few defined bytes for the split to mean much, and nothing else it settles.

    A split that pins fill bytes or a further bitmap is worth reporting however little of the
    record is decoded: that is how the Kona's missing battery-fan parameters show.
    """
    settled = bool(fit.fill) or len(fit.blocks) > 1
    return fit.claimed < MIN_CLAIMED_FRAC * fit.covered and not settled


def field_declare(scan: PidScan) -> str | None:
    fit = scan.field_fit
    if fit is None or fit.verdict == "broken":
        return None
    cmd = (
        f"canair pids set-support-map {scan.ecu} {scan.pid} "
        f"--at {fit.at} --length {fit.length} --unit field"
    )
    if fit.order != "msb":
        cmd += f" --order {fit.order}"
    more = [str(o) for o, _mask in fit.blocks[1:]]
    if more:
        cmd += f" --also-at {','.join(more)}"
    return cmd


def _labeller(scan: PidScan, notation: ByteNotation):
    def label(off: int) -> str:
        return offset_label(scan.labels, scan.header, scan.pid, len(scan.sample), off, notation)

    return label


def _span(label, start: int, end: int) -> str:
    return label(start) if end - start == 1 else f"{label(start)}-{label(end - 1)}"


def _parameters(fit: FieldFit, label) -> dict[str, list[str]]:
    """Open (marked, undefined) and unsupported parameters of a unique split, as labels."""
    size = 8 * fit.length
    out: dict[str, list[str]] = {"open": [], "unsupported": []}
    for f in fit.fields:
        if f.is_map:
            continue
        where = f"#{f.block * size + f.number} {_span(label, f.start, f.end)}"
        if not f.marked:
            out["unsupported"].append(where)
        elif not f.defined:
            out["open"].append(where)
    return out


def field_json(scan: PidScan, notation: ByteNotation) -> dict:
    fit = scan.field_fit
    assert fit is not None
    label = _labeller(scan, notation)
    doc = {
        "verdict": fit.verdict,
        "order": fit.order,
        "mask": fit.mask.hex().upper(),
        "maps": [o for o, _mask in fit.blocks],
        "marked": fit.marked,
        "covered": fit.covered,
        "claimed": fit.claimed,
        "splits": "unique" if fit.splits == 1 else ("several" if fit.splits else "none"),
        "parameters": list(fit.field_counts),
        "fill": [label(o) for o in sorted(fit.fill)],
        "reason": fit.reason or None,
        "declare": field_declare(scan),
    }
    if fit.fields:
        doc |= _parameters(fit, label)
    return doc


def _colour(verdict: str) -> str:
    """The code for a verdict, read at print time so ``NO_COLOR`` is honoured when it is set."""
    return {"exact": ansi.GREEN, "consistent": ansi.YELLOW, "broken": ansi.RED}[verdict]


def print_field_headline(scan: PidScan, notation: ByteNotation, head: str) -> None:
    """The report for a PID whose best reading is the field one."""
    fit = scan.field_fit
    assert fit is not None
    label = _labeller(scan, notation)
    order = "" if fit.order == "msb" else ", LSB first"
    print(
        f"{head}  {_colour(fit.verdict)}{fit.verdict.upper()}{ansi.RESET} per-field map "
        f"{ansi.BOLD}{' '.join(f'{b:02X}' for b in fit.mask)}{ansi.RESET} "
        f"{ansi.DIM}at data+{fit.at}{order}, {fit.n_rows} rows{ansi.RESET}"
    )
    lo, hi = fit.field_counts
    count = str(lo) if lo == hi else f"{lo}-{hi}"
    splits = "one split" if fit.splits == 1 else "several splits"
    print(
        f"      {fit.marked} parameters marked; {fit.covered} bytes hold {count} parameters "
        f"({splits}; signals read {fit.claimed} of the bytes)"
    )
    if fit.fields:
        params = _parameters(fit, label)
        print(f"      open, marked     {_join(params['open'])}")
        print(f"      unsupported      {_join(params['unsupported'])}")
    elif fit.fill:
        fill = _join([label(o) for o in sorted(fit.fill)])
        print(f"      fill             {fill} {ansi.DIM}(unmarked on every split){ansi.RESET}")
    if len(fit.blocks) > 1:
        more = ", ".join(f"data+{o} ({label(o)})" for o, _m in fit.blocks[1:])
        print(f"      more bitmaps     {more}")
    cmd = field_declare(scan)
    if cmd:
        print(f"      {ansi.DIM}declare: {cmd}{ansi.RESET}")


def print_field_note(scan: PidScan) -> None:
    """One line on the field reading, under a PID whose headline is another reading."""
    fit = scan.field_fit
    if fit is None:
        return
    order = "" if fit.order == "msb" else ", LSB first"
    why = f": {fit.reason}" if fit.reason else ""
    print(f"      {ansi.DIM}per-field reading{order}: {fit.verdict}{why}{ansi.RESET}")


def _join(labels: list[str], limit: int = 14) -> str:
    if not labels:
        return "-"
    shown = ", ".join(labels[:limit])
    return shown + (f", … +{len(labels) - limit}" if len(labels) > limit else "")
