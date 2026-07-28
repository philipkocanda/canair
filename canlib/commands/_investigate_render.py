#!/usr/bin/env python3
"""Presentation layer for ``canair investigate`` (extracted from investigate.py).

Pure rendering + the edge-timeline data gathering: the per-byte ranked report
(:func:`print_report`) and the ``--events`` edge/field timeline
(:func:`iter_edges`/:func:`iter_field_edges`/:func:`print_field_events`/
:func:`print_events`). Keeping these here leaves ``investigate.py`` as argparse +
the two orchestrators (``run``/``_run_can``) + the scoring math.

Reports are duck-typed ``_ByteReport`` instances (the command owns the
dataclass); these functions only read their attributes.
"""

from __future__ import annotations

import json as _json
import sys

from canlib.keepmode import scope_is_keep_unique
from canlib.notation import relabel_signal, resolve_notation, subfunction_bytes_for_pid

# ANSI colors — kept local (not imported from investigate) so this module has no
# import-time dependency on the command, which imports the renderers back.
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def cap_note(cap) -> str:
    """The best free-text note for a capture: its own note, else the session's."""
    return str(cap.get("notes") or cap.get("session_notes") or "").strip()


def print_keep_banner(captures) -> None:
    """Warn when the scope includes keep:unique sessions (rising-edge-only data)."""
    if scope_is_keep_unique(captures):
        print(
            f"    {_YELLOW}⚠ scope includes keep:unique sessions — only rising-edge "
            f"transitions were stored; falling edges/durations are absent.{_RESET}"
        )


def print_report(
    ecu, pid, reports, args, lp, has_anchors: bool, *, driver_label=None, words=None
) -> None:
    notation = resolve_notation(args.notation)
    sub_bytes = subfunction_bytes_for_pid(pid)
    print(
        f"\n  {_BOLD}Investigate {ecu} {pid}{_RESET} "
        f"{_DIM}({len(lp.captures)} timed captures, ≤{args.join_tol:g}s join){_RESET}"
    )
    print_keep_banner(lp.captures)
    if not reports:
        what = "varying " if not args.all else ""
        unit = "bytes/bits" if args.bits else "bytes"
        print(f"    {_DIM}no {what}{unit} to report{_RESET}\n")
        return
    for r in reports:
        if r.mapped_by is None:
            tag = f"{_YELLOW}unmapped{_RESET}"
        elif r.mapped_verified:
            tag = f"{_DIM}[{r.mapped_by}]{_RESET}"
        else:
            tag = f"{_YELLOW}[{r.mapped_by}?]{_RESET}"  # mapped but unverified — still open
        f_str = ""
        if r.state_f is not None:
            fc = _GREEN if r.state_f >= 10 else _YELLOW if r.state_f >= 2 else _DIM
            f_val = "∞" if r.state_f == float("inf") else f"{r.state_f:.1f}"
            f_str = f"  {fc}stateF={f_val}{_RESET}"
        anchor = ""
        if r.anchor and r.anchor_r is not None and abs(r.anchor_r) >= args.min_r:
            rc = _GREEN if abs(r.anchor_r) >= 0.7 else _YELLOW
            fit = f" fit y={r.slope:.4f}·x{r.intercept:+.2f}" if r.slope is not None else ""
            unit = f" {_CYAN}{r.unit_guess}{_RESET}" if r.unit_guess else ""
            anchor_label = relabel_signal(r.anchor, notation)
            anchor = f"  {rc}r={r.anchor_r:+.3f}{_RESET} vs {anchor_label} {_DIM}n={r.anchor_n}{fit}{_RESET}{unit}"
        drv = ""
        if driver_label is not None:
            # Low |driver r| = independent of the driver (the signal we want).
            dv = "—" if r.driver_r is None else f"{r.driver_r:+.3f}"
            dc = _GREEN if (r.driver_r is None or abs(r.driver_r) < 0.3) else _DIM
            drv = f"  {dc}drv={dv}{_RESET}"
        phys = f"  {_CYAN}{r.physical}{_RESET}" if r.physical else ""
        kind = ""
        if r.kind and r.bit is None and r.kind != "continuous":
            # Flag the non-analog byte classes (constant/counter/checksum/enum);
            # "continuous" is the unremarkable default, left unlabelled.
            kind = f"  {_DIM}{r.kind}{_RESET}"
        print(
            f"    {_BOLD}{relabel_signal(r.label, notation, sub_bytes=sub_bytes)}{_RESET} "
            f"{tag}{f_str}{drv}{anchor}{phys}{kind}"
        )
    if words:
        print(
            f"\n    {_BOLD}probable multi-byte words{_RESET} "
            f"{_DIM}(near-constant hi byte + full-range lo byte){_RESET}"
        )
        for w, expr in words[:6]:
            print(f"      {_CYAN}{expr}{_RESET}  {_DIM}score={w.score:.2f}{_RESET}")
    if driver_label is not None:
        print(
            f"    {_DIM}ranked by state separation × independence from {driver_label} "
            f"(drv = |r| vs that driver; low drv + high stateF = the target).{_RESET}"
        )
    if not has_anchors:
        # Body/comfort PIDs have no co-polled partner, so there is no anchor
        # column — that's expected, not "nothing here". Point at the right tool.
        print(
            f"    {_DIM}no co-polled anchor in scope — ranked by state separation. "
            f"For status bits try {_BOLD}--events{_RESET}{_DIM} (edge timeline).{_RESET}"
        )
    print()


def iter_edges(lp, mapped, mapped_bit, *, bits: bool):
    """Yield (dt, label, before, after, mapped_by, verified) for every value change.

    Walks each varying byte (and, with ``bits``, each toggling bit) in time order
    and emits one row per transition — the raw material for the event timeline.
    """
    from canlib.byteindex import payload_to_wican_bytes, wican_to_isotp
    from canlib.capture_dates import entry_datetime

    frames = []
    max_len = 0
    for cap in lp.captures:
        dt = entry_datetime(cap)
        if dt is None:
            continue
        try:
            fr = payload_to_wican_bytes(cap["payload"])
        except Exception:
            continue
        frames.append((dt, fr, cap))
        max_len = max(max_len, len(fr))
    frames.sort(key=lambda t: t[0])

    edges = []
    for off in range(max_len):
        if wican_to_isotp(off) is None:
            continue
        prev_byte: int | None = None
        prev_bit: dict[int, int] = {}
        for dt, fr, cap in frames:
            if off >= len(fr):
                continue
            val = fr[off]
            bit_edges_here = []
            if bits:
                for k in range(8):
                    b = (val >> k) & 1
                    pb = prev_bit.get(k)
                    if pb is not None and b != pb:
                        mb = mapped_bit.get((off, k))
                        bit_edges_here.append(
                            (
                                dt,
                                f"B{off}:{k}",
                                pb,
                                b,
                                mb[0] if mb else None,
                                mb[1] if mb else False,
                                cap,
                                "bit",
                            )
                        )
                    prev_bit[k] = b
            if prev_byte is not None and val != prev_byte:
                if bit_edges_here:
                    # The bit rows carry the same information at finer resolution;
                    # keep them and drop the redundant whole-byte edge.
                    edges.extend(bit_edges_here)
                elif bits:
                    # --bits on but no isolated bit mapped/toggling here — show the
                    # byte edge unattributed (a byte may hold many params).
                    edges.append((dt, f"B{off}", prev_byte, val, None, False, cap, "byte"))
                else:
                    # Byte-only mode: attribute to the covering param if any.
                    m = mapped.get(off)
                    edges.append(
                        (
                            dt,
                            f"B{off}",
                            prev_byte,
                            val,
                            m[0] if m else None,
                            m[1] if m else False,
                            cap,
                            "byte",
                        )
                    )
            elif bit_edges_here:
                edges.extend(bit_edges_here)
            prev_byte = val
    edges.sort(key=lambda e: e[0])
    return edges


def iter_field_edges(lp, param: dict):
    """Yield (dt, before_display, after_display, cap) per change of a typed param's
    DECODED value across timed captures — one logical transition per field change.

    This collapses a multi-byte/enum/bitmask/struct field into a single signal,
    so a schedule/mode change reads as ``{Mon 08:00}->{Tue 07:30}`` rather than
    scattered per-byte edges.
    """
    from canlib.byteindex import payload_to_wican_bytes
    from canlib.capture_dates import entry_datetime
    from canlib.decode_value import decode_typed, render

    frames = []
    for cap in lp.captures:
        dt = entry_datetime(cap)
        if dt is None:
            continue
        try:
            fr = payload_to_wican_bytes(cap["payload"])
        except Exception:
            continue
        frames.append((dt, fr, cap))
    frames.sort(key=lambda t: t[0])

    edges = []
    prev: str | None = None
    for dt, fr, cap in frames:
        disp = render(decode_typed(param, fr), param.get("unit", ""))
        if prev is not None and disp != prev:
            edges.append((dt, prev, disp, cap))
        prev = disp
    return edges


def print_field_events(ecu, pid, lp, args, param: dict) -> None:
    """Logical-transition timeline for one typed field (--events --field NAME)."""
    edges = iter_field_edges(lp, param)
    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "field": args.field,
                "keep_unique": scope_is_keep_unique(lp.captures),
                "events": [
                    {
                        "time": e[0].strftime("%H:%M:%S"),
                        "before": e[1],
                        "after": e[2],
                        "note": cap_note(e[3]),
                    }
                    for e in edges
                ],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return
    print(
        f"\n  {_BOLD}Events {ecu} {pid} · {args.field}{_RESET} "
        f"{_DIM}({len(lp.captures)} timed captures){_RESET}"
    )
    print_keep_banner(lp.captures)
    if not edges:
        print(f"    {_DIM}no transitions of {args.field} in scope.{_RESET}\n")
        return
    for dt, before, after, cap in edges:
        note = cap_note(cap)
        note_str = f"  {_DIM}~ note: {_CYAN}{note}{_RESET}" if note else ""
        print(
            f"    {_DIM}{dt.strftime('%H:%M:%S')}{_RESET}  "
            f"{_BOLD}{before}{_RESET} → {_BOLD}{after}{_RESET}{note_str}"
        )
    print()


def print_events(ecu, pid, lp, mapped, mapped_bit, args, params_def=None) -> None:
    """Edge/event-timeline view: each transition with its time and nearest note."""
    if args.field:
        params_def = params_def or {}
        param = params_def.get(args.field)
        if param is None:
            print(
                f"\n  {_YELLOW}investigate: no parameter {args.field!r} on {ecu} {pid}.{_RESET}\n",
                file=sys.stderr,
            )
            return
        print_field_events(ecu, pid, lp, args, param)
        return
    edges = iter_edges(lp, mapped, mapped_bit, bits=args.bits)
    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "keep_unique": scope_is_keep_unique(lp.captures),
                "events": [
                    {
                        "time": e[0].strftime("%H:%M:%S"),
                        "signal": e[1],
                        "before": e[2],
                        "after": e[3],
                        "mapped_by": e[4],
                        "verified": e[5],
                        "note": cap_note(e[6]),
                    }
                    for e in edges
                ],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return
    print(
        f"\n  {_BOLD}Events {ecu} {pid}{_RESET} {_DIM}({len(lp.captures)} timed captures){_RESET}"
    )
    print_keep_banner(lp.captures)
    if not edges:
        print(f"    {_DIM}no transitions in scope.{_RESET}\n")
        return
    notation = resolve_notation(args.notation)
    sub_bytes = subfunction_bytes_for_pid(pid)
    for dt, label, before, after, mapped_by, verified, cap, _kind in edges:
        if mapped_by is None:
            tag = f"{_YELLOW}candidate{_RESET}"
        elif verified:
            tag = f"{_DIM}[{mapped_by}]{_RESET}"
        else:
            tag = f"{_YELLOW}[{mapped_by}?]{_RESET}"
        note = cap_note(cap)
        note_str = f"  {_DIM}~ note: {_CYAN}{note}{_RESET}" if note else ""
        arrow = (
            f"{_BOLD}{before:#04x}→{after:#04x}{_RESET}" if _kind == "byte" else f"{before}→{after}"
        )
        shown = relabel_signal(label, notation, sub_bytes=sub_bytes)
        print(
            f"    {_DIM}{dt.strftime('%H:%M:%S')}{_RESET}  {_BOLD}{shown}{_RESET} {arrow}  {tag}{note_str}"
        )
    print()
