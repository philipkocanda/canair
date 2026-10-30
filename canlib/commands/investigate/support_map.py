"""``investigate --support-map`` — find the leading bytes that mark a PID's real signals.

Bridges the pure detector in :mod:`canlib.analysis.support_map` to the capture model: builds the
row-aligned ISO-TP payload matrix, strips the SID and DID echo, scores every candidate placement,
then lays the result over the PID's defined signals so the report says which marked positions are
still open work. Sibling DIDs of an ECU that share a placement corroborate one another, because a
single DID with a handful of set bits cannot be significant alone.

Read-only: it proposes a declaration (``canair pids set-support-map``), it never writes one.
"""

from __future__ import annotations

import json as _json
import sys
from collections import Counter
from dataclasses import dataclass, field

from canlib import ansi
from canlib.analysis.align import LoadedPid, discover_signal_specs, load_signal_captures
from canlib.analysis.support_map import (
    MIN_EXPLAINED,
    MapFit,
    Slot,
    best_fit,
    corroborated,
    family_chance_log10,
)
from canlib.uds.byteindex import (
    declared_bits,
    extract_byte_indices,
    payload_index_to_wican,
    references_full_byte,
)
from canlib.uds.notation import (
    ByteNotation,
    relabel_signal,
    resolve_notation,
    subfunction_bytes_for_pid,
)

from ._matrix import payload_matrix

# (name, verified) of whatever defines a position
Definer = tuple[str, bool]


@dataclass
class PidScan:
    """The detection result for one PID."""

    ecu: str
    pid: str
    n_rows: int
    header: int
    sample: bytes
    fits: list[MapFit]
    best: MapFit | None
    params: dict
    verdict: str = "none"
    family_log10: float = 0.0
    labels: dict[int, int] = field(default_factory=dict)


def _modal_payload(lp: LoadedPid) -> bytes:
    """The most common full-length payload, so the frame layout matches the real replies."""
    lengths = Counter(len(d.payload_bytes) for d in lp.decoded)
    if not lengths:
        return b""
    n = max(lengths, key=lambda k: (lengths[k], k))
    return next(d.payload_bytes for d in lp.decoded if len(d.payload_bytes) == n)


def scan_pid(ecu: str, pid: str, lp: LoadedPid, params: dict, args) -> PidScan | None:
    """Detect support-map placements on one PID; None when there are too few timed captures."""
    from canlib.analysis.support_map import detect_support_maps

    _dts, payloads = payload_matrix(lp)
    if not payloads:
        return None
    header = 1 + subfunction_bytes_for_pid(pid)
    rows = [p[header:] for p in payloads if len(p) > header]
    fits = detect_support_maps(rows, max_at=args.map_max_offset)
    sample = _modal_payload(lp)
    best = best_fit(fits)
    return PidScan(
        ecu, pid, len(rows), header, sample, fits, best, params,
        labels=payload_index_to_wican(list(sample)),
    )  # fmt: skip


def judge(scans: dict[str, PidScan], args) -> None:
    """Set each scan's verdict, letting sibling DIDs corroborate a weak-but-consistent fit."""

    def rate(fit: MapFit) -> str:
        return fit.verdict(max_chance=args.map_max_chance, max_violations=args.map_allow_violations)

    usable = {
        pid: s.best
        for pid, s in scans.items()
        if s.best is not None and rate(s.best) in ("strong", "weak")
    }
    promoted = corroborated(usable, max_chance=args.map_max_chance)
    for pid, scan in scans.items():
        fit = scan.best
        if fit is None:
            scan.verdict = "none"
            continue
        scan.verdict = "corroborated" if pid in promoted else rate(fit)
        siblings = [f for p, f in usable.items() if f.key == fit.key]
        scan.family_log10 = family_chance_log10(siblings) if fit in siblings else 0.0


def _definers(params: dict) -> tuple[dict[tuple[int, int], Definer], dict[int, Definer]]:
    """WiCAN positions the PID's signals read: ``(byte, bit) -> definer`` and whole bytes."""
    bits: dict[tuple[int, int], Definer] = {}
    whole: dict[int, Definer] = {}
    for name, pdef in params.items():
        expr = pdef.get("expression") or ""
        who = (str(name), bool(pdef.get("verified")))
        for byte, ks in declared_bits(pdef).items():
            for k in ks:
                bits.setdefault((byte, k), who)
        for byte in extract_byte_indices(expr):
            if references_full_byte(expr, byte):
                whole.setdefault(byte, who)
    return bits, whole


def _slot_definer(scan: PidScan, slot: Slot) -> Definer | None:
    """What defines a slot. A whole-byte read defines a per-byte slot, never a single bit."""
    bits, whole = _definers(scan.params)
    w = scan.labels.get(scan.header + slot.offset)
    if w is None:
        return None
    if slot.bit is not None:
        return bits.get((w, slot.bit))
    return whole.get(w) or next((v for (b, _k), v in bits.items() if b == w), None)


def _label(scan: PidScan, slot: Slot, notation: ByteNotation) -> str:
    w = scan.labels.get(scan.header + slot.offset)
    base = f"B{w}" if w is not None else f"i{scan.header + slot.offset}"
    text = base if slot.bit is None else f"{base}:{slot.bit}"
    return relabel_signal(
        text, notation, sub_bytes=subfunction_bytes_for_pid(scan.pid),
        payload_len=len(scan.sample) or None,
    )  # fmt: skip


def _outside_defined(scan: PidScan, fit: MapFit) -> list[tuple[str, str, bool]]:
    """Defined signals that read a position the map does not mark: (name, where, continuation)."""
    from canlib.analysis.support_map import continuation_bits, marked_bits

    if fit.unit != "bit":
        return []
    by_w = {w: p for p, w in scan.labels.items()}
    out: list[tuple[str, str, bool]] = []
    bits, _whole = _definers(scan.params)
    per_byte: dict[int, set[int]] = {}
    for (w, k), _who in bits.items():
        per_byte.setdefault(w, set()).add(k)
    for w, ks in sorted(per_byte.items()):
        pidx = by_w.get(w)
        if pidx is None:
            continue
        off = pidx - scan.header
        j = off - fit.covered_start
        if not 0 <= j < fit.covered_len:
            continue
        marks = marked_bits(fit.mask[j])
        cont = continuation_bits(marks, ks - marks)
        for k in sorted(ks - marks):
            out.append((bits[(w, k)][0], f"B{w}:{k}", k in cont))
    return out


def _as_json(scan: PidScan, args) -> dict:
    fit = scan.best
    base = {"ecu": scan.ecu, "pid": scan.pid, "verdict": scan.verdict, "rows": scan.n_rows}
    if fit is None:
        return base
    notation = resolve_notation(args.notation)

    def sig(s: Slot) -> dict:
        who = _slot_definer(scan, s)
        return {
            "label": _label(scan, s, notation),
            "offset": s.offset,
            "bit": s.bit,
            "state": s.state,
            "defined_by": who[0] if who else None,
            "verified": who[1] if who else None,
        }

    return base | {
        "unit": fit.unit,
        "at": fit.at,
        "length": fit.length,
        "mask": fit.mask.hex().upper(),
        "chance_log10": round(fit.penalised_log10, 2),
        "family_log10": round(scan.family_log10, 2),
        "marked": [sig(s) for s in fit.marked],
        "continuations": [sig(s) for s in fit.continuations],
        "violations": [sig(s) for s in fit.violations],
        "outside_defined": [
            {"signal": n, "where": w, "continuation": c} for n, w, c in _outside_defined(scan, fit)
        ],
        "declare": declare_command(scan),
    }


def declare_command(scan: PidScan) -> str | None:
    fit = scan.best
    if fit is None or scan.verdict in ("none", "rejected"):
        return None
    return (
        f"canair pids set-support-map {scan.ecu} {scan.pid} "
        f"--at {fit.at} --length {fit.length} --unit {fit.unit}"
    )


_VERDICT_COLOUR = {
    "strong": ansi.GREEN,
    "corroborated": ansi.GREEN,
    "weak": ansi.YELLOW,
    "rejected": ansi.RED,
    "none": ansi.DIM,
}


def _join(labels: list[str], limit: int = 14) -> str:
    if not labels:
        return "-"
    shown = ", ".join(labels[:limit])
    return shown + (f", … +{len(labels) - limit}" if len(labels) > limit else "")


def _print_scan(scan: PidScan, args) -> None:
    fit = scan.best
    colour = _VERDICT_COLOUR[scan.verdict]
    head = f"  {ansi.BOLD}{ansi.CYAN}{scan.ecu} {scan.pid}{ansi.RESET}"
    if fit is None:
        print(f"{head}  {ansi.DIM}no constant leading map found ({scan.n_rows} rows){ansi.RESET}")
        return
    notation = resolve_notation(args.notation)
    marked = fit.marked
    defined = [s for s in marked if _slot_definer(scan, s)]
    open_ = [s for s in marked if not _slot_definer(scan, s)]
    by_state: dict[str, list[str]] = {}
    for s in open_:
        by_state.setdefault(s.state, []).append(_label(scan, s, notation))
    kind = "per-bit" if fit.unit == "bit" else "per-byte"
    evidence = f"chance 10^{fit.penalised_log10:.1f}"
    if scan.family_log10:
        evidence += f", with siblings 10^{scan.family_log10:.1f}"
    print(
        f"{head}  {colour}{scan.verdict.upper()}{ansi.RESET} {kind} map "
        f"{ansi.BOLD}{' '.join(f'{b:02X}' for b in fit.mask)}{ansi.RESET} "
        f"{ansi.DIM}at data+{fit.at}, {fit.n_rows} rows, {evidence}{ansi.RESET}"
    )
    print(f"      {len(marked)} signals marked: {len(defined)} defined, {len(open_)} open")
    moving = by_state.pop("moving", []) + by_state.pop("live", []) + by_state.pop("toggling", [])
    print(f"      open, moving     {_join(moving)}")
    print(
        f"      open, always on  {_join(by_state.pop('always', []) + by_state.pop('constant', []))}"
    )
    print(f"      open, never set  {_join(by_state.pop('never', []) + by_state.pop('zero', []))}")
    if fit.continuations:
        extra = _join([_label(scan, s, notation) for s in fit.continuations])
        print(
            f"      field continuation  {extra} {ansi.DIM}(set, unmarked, directly above a mark){ansi.RESET}"
        )
    if fit.violations:
        bad = _join([_label(scan, s, notation) for s in fit.violations])
        print(f"      {ansi.RED}outside the map{ansi.RESET}  {bad}")
    outside = [o for o in _outside_defined(scan, fit) if not o[2]]
    if outside:
        names = _join([f"{n} ({w})" for n, w, _c in outside])
        print(f"      {ansi.RED}defined signals outside the map{ansi.RESET}  {names}")
    cmd = declare_command(scan)
    if cmd:
        print(f"      {ansi.DIM}declare: {cmd}{ansi.RESET}")
    elif scan.verdict == "rejected":
        print(f"      {ansi.DIM}closest fit only; not proposed for declaration{ansi.RESET}")


def run_support_map(specs: list[tuple[str, str]], args, since, until) -> int:
    """Detect and report support maps for ``specs`` (siblings on the same ECU corroborate)."""
    from canlib.pids import build_ecu_index, load_pids

    if not specs:
        print("No timed captures in scope to investigate.", file=sys.stderr)
        return 1
    ecu_index = build_ecu_index(load_pids())
    scans: list[PidScan] = []
    for ecu in dict.fromkeys(e for e, _p in specs):
        siblings = discover_signal_specs(ecu, since=since, until=until, state=args.state,
                                         label=args.label)  # fmt: skip
        wanted = {p for e, p in specs if e == ecu}
        pairs = sorted({(ecu, p) for p in wanted} | set(siblings))
        loaded = load_signal_captures(pairs, since=since, until=until, state=args.state,
                                      label=args.label)  # fmt: skip
        per_pid: dict[str, PidScan] = {}
        for _e, pid in pairs:
            lp = loaded.get((ecu.upper(), pid))
            params = ecu_index.get(ecu.upper(), {}).get("pids", {}).get(pid, {})
            scan = (
                scan_pid(ecu.upper(), pid, lp, params.get("parameters", {}), args) if lp else None
            )
            if scan is not None:
                per_pid[pid] = scan
        judge(per_pid, args)
        scans += [s for pid, s in per_pid.items() if pid in wanted]

    def noise(scan: PidScan) -> bool:
        """A sweep row with too little behind it to mention."""
        fit = scan.best
        if fit is None or scan.verdict == "none":
            return True
        thin = fit.inside < MIN_EXPLAINED or fit.penalised_log10 > 0
        return scan.verdict in ("weak", "rejected") and thin

    sweeping = len(scans) > 1 and not args.map_all
    shown = [s for s in scans if not (sweeping and noise(s))]
    if args.json:
        _json.dump({"mode": "support_map", "pids": [_as_json(s, args) for s in shown]},
                   sys.stdout, indent=2, default=str)  # fmt: skip
        print()
        return 0
    found = sum(1 for s in scans if s.verdict in ("strong", "corroborated"))
    print(
        f"\n{ansi.BOLD}Support maps{ansi.RESET} {ansi.DIM}— {found} of {len(scans)} PID(s) carry "
        f"one{ansi.RESET}\n"
    )
    order = {"strong": 0, "corroborated": 1, "weak": 2, "rejected": 3, "none": 4}
    for scan in sorted(shown, key=lambda s: (order[s.verdict], s.ecu, s.pid)):
        _print_scan(scan, args)
        print()
    return 0
