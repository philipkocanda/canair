"""Rendering for ``investigate --support-map``: the text report and ``--json``.

Split from :mod:`.support_map` (scanning and verdicts) so neither grows past one concern. The
field-map lines come from :mod:`.support_fields`.
"""

from __future__ import annotations

from collections import Counter

from canair.analysis.support_map import ABSENCE_TEXT, MapFit, Slot
from canair.cli.args.notation import resolve_notation
from canair.render import ansi
from canair.uds.byteindex import declared_bits, extract_byte_indices, references_full_byte
from canair.uds.notation import ByteNotation, relabel_signal, subfunction_bytes_for_pid

from .support_fields import field_declare, field_json, print_field_headline, print_field_note
from .support_map import PidScan

# (name, verified) of whatever defines a position
Definer = tuple[str, bool]


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
    from canair.uds.support_marks import continuation_bits, marked_bits

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


def as_json(scan: PidScan, args) -> dict:
    """One PID's ``--json`` record: the per-bit/per-byte fit, the field reading, variants."""
    notation = resolve_notation(args.notation)
    base = {"ecu": scan.ecu, "pid": scan.pid, "verdict": scan.verdict, "rows": scan.n_rows}
    if scan.lead:
        base["lead"] = scan.lead.hex().upper()
    if scan.field_fit is not None:
        base["field"] = field_json(scan, notation)
    if scan.variants:
        base["variants"] = [as_json(v, args) for v in scan.variants]
        base["variant_diff"] = variant_diff(scan)
    fit = scan.best
    if fit is None:
        if scan.absence and scan.verdict == "none":
            base |= {"absence": scan.absence, "absence_text": ABSENCE_TEXT[scan.absence]}
        return base

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
    """The ``pids set-support-map`` command for the reading the verdict rests on, if any."""
    if scan.verdict in ("exact", "consistent"):
        return field_declare(scan)
    fit = scan.best
    if fit is None or scan.verdict not in ("strong", "corroborated", "weak"):
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


def variant_diff(scan: PidScan) -> list[int]:
    """Map bit numbers (1 = bit 7 of the first byte) set in some variants' leads, not all."""
    leads = [v.lead for v in scan.variants]
    if len(leads) < 2:
        return []
    width = min(len(lead) for lead in leads)
    out = []
    for k in range(8 * width):
        bits = {(lead[k // 8] >> (7 - k % 8)) & 1 for lead in leads}
        if len(bits) > 1:
            out.append(k + 1)
    return out


def _print_variants(scan: PidScan, args, head: str) -> None:
    print(
        f"{head}  {ansi.YELLOW}VARIANTS{ansi.RESET} {len(scan.variants)} maps in the captures "
        f"{ansi.DIM}({scan.n_rows} rows; each fitted on its own rows){ansi.RESET}"
    )
    diff = variant_diff(scan)
    if diff:
        print(
            f"      differ in map bit(s) {', '.join(f'#{k}' for k in diff)} "
            f"{ansi.DIM}(a parameter or signal one unit has and another lacks){ansi.RESET}"
        )
    for sub in scan.variants:
        print_scan(sub, args)


def _join(labels: list[str], limit: int = 14) -> str:
    if not labels:
        return "-"
    shown = ", ".join(labels[:limit])
    return shown + (f", … +{len(labels) - limit}" if len(labels) > limit else "")


def print_scan(scan: PidScan, args) -> None:
    """The text report for one PID."""
    fit = scan.best
    notation = resolve_notation(args.notation)
    lead = f" [{scan.lead.hex().upper()}]" if scan.lead else ""
    indent = "    " if scan.lead else "  "
    head = f"{indent}{ansi.BOLD}{ansi.CYAN}{scan.ecu} {scan.pid}{lead}{ansi.RESET}"
    if scan.variants:
        _print_variants(scan, args, head)
        return
    if scan.verdict in ("exact", "consistent"):
        print_field_headline(scan, notation, head)
        return
    if fit is None:
        why = ABSENCE_TEXT[scan.absence] if scan.absence else "no constant leading map found"
        print(f"{head}  {ansi.DIM}no map: {why} ({scan.n_rows} rows){ansi.RESET}")
        print_field_note(scan)
        return
    colour = _VERDICT_COLOUR[scan.verdict]
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
    if scan.verdict not in ("strong", "corroborated") and fit.unit != "bit":
        # A per-bit fit is the bitmapped layout; a field reading of it is only noise.
        print_field_note(scan)


_ABSENCE_SHORT: dict[str, str] = {
    "varies": "data right after the DID echo",
    "variants": "map differs between captures",
    "empty": "constant 00",
    "full": "constant FF",
    "still": "nothing moves",
    "few-rows": "too few captures",
}


def absence_tally(scans: list[PidScan]) -> str:
    """One line counting why the PIDs without a map have none, most informative first."""
    counts: Counter[str] = Counter(
        str(s.absence)
        for s in scans
        if s.best is None and s.absence and s.verdict not in ("exact", "consistent", "variants")
    )
    return ", ".join(f"{counts[k]} {text}" for k, text in _ABSENCE_SHORT.items() if counts[k])
