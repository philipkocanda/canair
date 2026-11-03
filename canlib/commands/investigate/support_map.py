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
from canlib.analysis.support_fields import FieldFit
from canlib.analysis.support_map import (
    MAX_MAP_LEN,
    MIN_EXPLAINED,
    MIN_ROWS,
    Absence,
    MapFit,
    absence_reason,
    best_fit,
    corroborated,
    detect_support_maps,
    family_chance_log10,
)
from canlib.uds.byteindex import payload_index_to_wican
from canlib.uds.notation import subfunction_bytes_for_pid

from ._matrix import payload_matrix
from .support_fields import fit_fields, thin

# Best first: the order a report lists PIDs in.
VERDICT_ORDER = (
    "strong", "corroborated", "exact", "variants", "consistent", "weak", "rejected", "none",
)  # fmt: skip


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
    absence: Absence | None = None  # why there is no fit, when there is none
    field_fit: FieldFit | None = None  # the one-bit-per-parameter reading (support_fields)
    # A PID whose captures carry several maps (two cars, say): one scan per map, and the lead
    # this scan was fitted on.
    variants: list[PidScan] = field(default_factory=list)
    lead: bytes = b""


def _modal_payload(lp: LoadedPid) -> bytes:
    """The most common full-length payload, so the frame layout matches the real replies."""
    lengths = Counter(len(d.payload_bytes) for d in lp.decoded)
    if not lengths:
        return b""
    n = max(lengths, key=lambda k: (lengths[k], k))
    return next(d.payload_bytes for d in lp.decoded if len(d.payload_bytes) == n)


def scan_pid(ecu: str, pid: str, lp: LoadedPid, params: dict, args) -> PidScan | None:
    """Detect support-map placements on one PID; None when there are too few timed captures."""
    _dts, payloads = payload_matrix(lp)
    if not payloads:
        return None
    header = 1 + subfunction_bytes_for_pid(pid)
    rows = [p[header:] for p in payloads if len(p) > header]
    scan = _scan_rows(ecu, pid, rows, header, _modal_payload(lp), params, args)
    if scan.absence == "variants":
        width = min(len(r) for r in rows)
        groups = Counter(r[: min(MAX_MAP_LEN, width)] for r in rows)
        for lead, n in groups.most_common():
            if n >= MIN_ROWS:
                use = [r for r in rows if r[: len(lead)] == lead]
                sub = _scan_rows(ecu, pid, use, header, scan.sample, params, args)
                sub.lead = lead
                scan.variants.append(sub)
    return scan


def _scan_rows(
    ecu: str, pid: str, rows: list[bytes], header: int, sample: bytes, params: dict, args
) -> PidScan:
    """Fit every reading to ``rows`` (data bytes, SID and DID echo stripped)."""
    fits = detect_support_maps(rows, max_at=args.map_max_offset)
    best = best_fit(fits)
    scan = PidScan(
        ecu, pid, len(rows), header, sample, fits, best, params,
        labels=payload_index_to_wican(list(sample)),
        absence=None if best else absence_reason(rows, max_at=args.map_max_offset),
    )  # fmt: skip
    scan.field_fit = fit_fields(scan, rows, args.map_max_offset)
    return scan


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
        if fit is not None:
            scan.verdict = "corroborated" if pid in promoted else rate(fit)
            siblings = [f for p, f in usable.items() if f.key == fit.key]
            scan.family_log10 = family_chance_log10(siblings) if fit in siblings else 0.0
        _prefer_fields(scan)
        for sub in scan.variants:
            sub.verdict = rate(sub.best) if sub.best is not None else "none"
            _prefer_fields(sub)
        if scan.variants:
            scan.verdict = "variants"


def _prefer_fields(scan: PidScan) -> None:
    """Let the field reading speak where the per-bit and per-byte ones have nothing better.

    An exact split beats anything short of strong or corroborated; a consistent one only
    replaces a rejected fit or no fit (a weak per-byte fit is the more specific claim).
    """
    fit = scan.field_fit
    if fit is None or fit.verdict == "broken" or scan.verdict in ("strong", "corroborated"):
        return
    if fit.verdict == "exact" or scan.verdict in ("rejected", "none"):
        scan.verdict = fit.verdict


def run_support_map(specs: list[tuple[str, str]], args, since, until) -> int:
    """Detect and report support maps for ``specs`` (siblings on the same ECU corroborate)."""
    from canlib.pids import build_ecu_index, load_pids

    # The report module imports PidScan from here, so it is imported where it is used.
    from .support_map_report import absence_tally, as_json, print_scan

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
                                      label=args.label, one_revision="investigate")  # fmt: skip
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
        if scan.verdict == "variants":
            return False
        if scan.verdict in ("exact", "consistent"):
            fit = scan.field_fit
            return fit is not None and scan.verdict == "consistent" and thin(fit)
        fit = scan.best
        if fit is None or scan.verdict == "none":
            return True
        weak = fit.inside < MIN_EXPLAINED or fit.penalised_log10 > 0
        return scan.verdict in ("weak", "rejected") and weak

    sweeping = len(scans) > 1 and not args.map_all
    shown = [s for s in scans if not (sweeping and noise(s))]
    if args.json:
        _json.dump({"mode": "support_map", "pids": [as_json(s, args) for s in shown]},
                   sys.stdout, indent=2, default=str)  # fmt: skip
        print()
        return 0
    found = sum(1 for s in scans if s.verdict in ("strong", "corroborated", "exact"))
    loose = sum(1 for s in scans if s.verdict in ("consistent", "variants"))
    more = f", {loose} more fit one loosely or carry several" if loose else ""
    print(
        f"\n{ansi.BOLD}Support maps{ansi.RESET} {ansi.DIM}— {found} of {len(scans)} PID(s) carry "
        f"one{more}{ansi.RESET}"
    )
    tally = absence_tally(scans)
    if tally and len(scans) > 1:
        print(f"{ansi.DIM}No map: {tally}{ansi.RESET}")
    print()
    for scan in sorted(shown, key=lambda s: (VERDICT_ORDER.index(s.verdict), s.ecu, s.pid)):
        print_scan(scan, args)
        print()
    return 0
