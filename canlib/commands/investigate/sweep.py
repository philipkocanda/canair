"""``canair investigate`` corpus/ECU sweep — the summary view over many PIDs.

When ``investigate`` is given an ECU (or a QUERY) instead of a single PID, or no
positional at all, it sweeps every matching captured PID. The per-byte deep-dive
is far too verbose to repeat N times, so a sweep renders a **ranked summary**:

- ``--counters`` → one ranked list of every monotonic counter found across the
  scope ("find every counter in this car"), reusing :func:`scan_counters`.
- default → one line per PID with the cheap, *local* facts that make a PID worth
  a deep-dive (varying/unmapped byte counts, best state-separation F, physical-
  band hits). The expensive cross-PID anchor correlation is deliberately left to
  the single-PID view — computing it for every pair of PIDs would be O(PIDs²).

The narrated timelines (``--events``/``--dwell``/``--field``) are single-signal
by nature, so they refuse a sweep and point back at a single ``ECU PID``.
"""

from __future__ import annotations

import json as _json
import sys

from canlib.align import LoadedPid, load_signal_captures
from canlib.byteindex import mapped_offsets
from canlib.notation import ByteNotation, resolve_notation, subfunction_bytes_for_pid

from .counters import (
    _display_label,
    _expression,
    _mapped_by,
    scan_counters,
    warn_scoped_counters,
)

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def run_sweep(specs: list[tuple[str, str]], args, since, until, fill) -> int:
    """Sweep every matching captured PID and print a ranked summary."""
    if args.events or args.dwell or args.field:
        print(
            "investigate: --events/--dwell/--field are per-signal timelines and need "
            "a single ECU PID (they don't summarise across a sweep).",
            file=sys.stderr,
        )
        return 2
    if not specs:
        print("No timed captures in scope to investigate.", file=sys.stderr)
        return 1

    from canlib.pids import build_ecu_index, load_pids

    ecu_index = build_ecu_index(load_pids())
    loaded = load_signal_captures(
        specs, since=since, until=until, state=args.state, label=args.label
    )

    def params_of(ecu: str, pid: str) -> dict:
        return ecu_index.get(ecu, {}).get("pids", {}).get(pid, {}).get("parameters", {})

    if args.counters:
        return _sweep_counters(specs, loaded, args, params_of)
    return _sweep_default(specs, loaded, args, params_of, fill)


# ── --counters sweep ─────────────────────────────────────────────────────────


def _sweep_counters(specs, loaded, args, params_of) -> int:
    from canlib.capture_dates import active_scope_flags

    scope_flags = active_scope_flags(args)
    if scope_flags:
        print(warn_scoped_counters(scope_flags), file=sys.stderr)

    notation = resolve_notation(args.notation)
    # One row per (ECU, PID, counter window); ranked across the whole scope by
    # monotonic evidence so the strongest counters in the car float to the top.
    rows = []
    n_scanned = 0
    for ecu, pid in specs:
        lp = loaded.get((ecu, pid))
        if lp is None or not lp.captures:
            continue
        scan = scan_counters(pid, lp, args, params_of(ecu, pid))
        if scan is None:
            continue
        n_scanned += 1
        for rep, _members in scan.groups:
            rows.append((ecu, pid, rep, scan.mapped))
    rows.sort(key=lambda r: -r[2].bits)
    if args.top:
        shown = rows[: args.top]
    else:
        shown = rows

    if args.json:
        _json.dump(
            {
                "mode": "counters",
                "scoped": bool(scope_flags),
                "pids_scanned": n_scanned,
                "counters_found": len(rows),
                "counters": [
                    {
                        "ecu": ecu,
                        "pid": pid,
                        "expression": _expression(rep),
                        "label": _display_label(rep, ByteNotation.ISOTP, 1),
                        "kind": rep.kind,
                        "bits": round(rep.bits, 2),
                        "first": rep.first,
                        "last": rep.last,
                        "mapped_by": _mapped_by(rep, mapped)[0],
                        "mapped_verified": _mapped_by(rep, mapped)[1],
                    }
                    for ecu, pid, rep, mapped in shown
                ],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    print(
        f"\n  {_BOLD}Monotonic counters{_RESET} {_DIM}— {len(rows)} found across "
        f"{n_scanned} PID(s){_RESET}"
    )
    if not rows:
        print(
            f"    {_DIM}No counter-like window in scope. Grow the capture set, or "
            f"lower --min-bits on a single PID to see near-misses.{_RESET}\n"
        )
        return 0
    for ecu, pid, rep, mapped in shown:
        expr = _expression(rep) or _display_label(rep, notation, subfunction_bytes_for_pid(pid))
        mapped_by, mapped_verified = _mapped_by(rep, mapped)
        if mapped_by is None:
            tag = f"{_YELLOW}UNMAPPED{_RESET}"
        elif mapped_verified:
            tag = f"{_DIM}[{mapped_by}]{_RESET}"
        else:
            tag = f"{_YELLOW}[{mapped_by}?]{_RESET}"
        print(
            f"    {_BOLD}{ecu:<6} {pid:<8}{_RESET} {_GREEN}{expr}{_RESET}  "
            f"{_DIM}{rep.kind} bits={rep.bits:.1f}{_RESET}  {rep.first:.0f}→{rep.last:.0f}  {tag}"
        )
    if args.top and len(rows) > args.top:
        print(f"    {_DIM}… (+{len(rows) - args.top} more; raise --top){_RESET}")
    print()
    return 0


# ── default per-byte sweep summary ─────────────────────────────────────────────


def _pid_summary(lp: LoadedPid, params_def: dict, args, fill) -> dict | None:
    """Cheap, LOCAL per-PID facts for the sweep summary (no cross-PID joins)."""
    from canlib.commands.investigate.report import _state_f
    from canlib.grid_prompt import resolve_grid_region
    from canlib.physical_bands import resolve_physical_bands
    from canlib.profile import active
    from canlib.xanalysis import build_byte_series, byte_state_buckets, physical_scan

    target = build_byte_series(lp, min_distinct=2, fill=fill)
    if not target:
        return None
    mapped = mapped_offsets(params_def)
    all_results = [{"capture": c} for c in lp.captures]
    buckets = byte_state_buckets(all_results, "state")

    n_varying = len(target)
    n_unmapped = 0
    best_f = 0.0
    for key in target:
        off = int(key.rsplit(":B", 1)[1])
        if off not in mapped or not mapped[off][1]:  # unmapped or unverified-mapped
            n_unmapped += 1
        sb = buckets.get(f"B{off}")
        f = _state_f(sb) if sb else None
        if f is not None and f != float("inf"):
            best_f = max(best_f, f)
        elif f == float("inf"):
            best_f = float("inf")

    bands = resolve_physical_bands(active().meta, grid_region=resolve_grid_region())
    n_physical = len(physical_scan(lp, bands=bands))
    return {
        "varying": n_varying,
        "unmapped": n_unmapped,
        "best_state_f": best_f,
        "physical": n_physical,
    }


def _sweep_default(specs, loaded, args, params_of, fill) -> int:
    rows: list[tuple[str, str, dict]] = []
    for ecu, pid in specs:
        lp = loaded.get((ecu, pid))
        if lp is None or not lp.captures:
            continue
        summary = _pid_summary(lp, params_of(ecu, pid), args, fill)
        if summary is not None:
            rows.append((ecu, pid, summary))

    # Rank by promise: highest state separation, then most undecoded bytes.
    def rank(row):
        s = row[2]
        f = 1e9 if s["best_state_f"] == float("inf") else s["best_state_f"]
        return (-f, -s["unmapped"], -s["physical"])

    rows.sort(key=rank)
    shown = rows[: args.top] if args.top else rows

    if args.json:
        _json.dump(
            {
                "mode": "summary",
                "pids": [
                    {
                        "ecu": ecu,
                        "pid": pid,
                        **{k: (None if v == float("inf") else v) for k, v in s.items()},
                        "state_f_inf": s["best_state_f"] == float("inf"),
                    }
                    for ecu, pid, s in shown
                ],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    print(
        f"\n  {_BOLD}Investigate sweep{_RESET} {_DIM}— {len(rows)} PID(s) with varying "
        f"bytes, ranked by decodability{_RESET}"
    )
    if not rows:
        print(f"    {_DIM}No varying data bytes in scope.{_RESET}\n")
        return 0
    for ecu, pid, s in shown:
        f = s["best_state_f"]
        f_str = "∞" if f == float("inf") else f"{f:.1f}"
        fc = _GREEN if (f == float("inf") or f >= 10) else _YELLOW if f >= 2 else _DIM
        phys = f"  {_CYAN}phys={s['physical']}{_RESET}" if s["physical"] else ""
        print(
            f"    {_BOLD}{ecu:<6} {pid:<8}{_RESET} "
            f"{_DIM}varying={s['varying']:<3} unmapped={s['unmapped']:<3}{_RESET} "
            f"{fc}topF={f_str}{_RESET}{phys}"
        )
    if args.top and len(rows) > args.top:
        print(f"    {_DIM}… (+{len(rows) - args.top} more; raise --top){_RESET}")
    print(
        f"\n  {_DIM}Deep-dive a promising PID with {_RESET}"
        f"canair investigate <ECU> <PID>{_DIM} (adds cross-signal anchors).{_RESET}"
    )
    return 0
