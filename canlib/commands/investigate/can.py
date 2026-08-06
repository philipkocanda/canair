"""``investigate can`` — the domain-B kind, over one arbitration ID in a frame log.

The same per-byte ranking against a reference frame byte in the same raw
broadcast-CAN log. Labels are raw-CAN ``rN`` — no WiCAN indices, no PCI — and
there is no ``--promote`` (broadcast signals are defined in ``signals/``).
"""

from __future__ import annotations

import json as _json
import sys

from canlib.align import (
    prepare_series,
)

from .report import _best_anchor

NAME = "investigate"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def _run_can(args) -> int:
    """Explain one arbitration ID in a frame log — per-byte best cross-ID anchor.

    Domain-B analogue of ``run``: build the target ID's byte (and, with --bits,
    bit) series and every OTHER ID's series as anchors, then rank each target
    byte by its strongest cross-ID correlation (r + linear fit + unit guess).
    """
    from pathlib import Path

    from canlib import frame_series
    from canlib.can_logs import CanLogError

    path = Path(args.file)
    if not path.is_file():
        print(f"investigate can: no such file: {path}", file=sys.stderr)
        return 1
    try:
        target_ids = frame_series.parse_id_filter(args.id)
        if target_ids is None or len(target_ids) != 1:
            print(
                f"investigate can: --id must be a single arbitration ID, got {args.id!r}",
                file=sys.stderr,
            )
            return 1
        # Build ALL varying series once (target + anchors share the single read).
        if args.bits:
            all_series = frame_series.build_frame_bit_series(path, args.can_format)
        else:
            all_series = frame_series.build_frame_series(path, args.can_format, min_distinct=4)
    except (ValueError, CanLogError) as e:
        print(f"investigate can: {e}", file=sys.stderr)
        return 1

    target_label = frame_series._id_label(next(iter(target_ids)))
    target = {n: s for n, s in all_series.items() if n.split(":", 1)[0] == target_label}
    anchors = {n: s for n, s in all_series.items() if n.split(":", 1)[0] != target_label}
    if not target:
        print(
            f"investigate can: {target_label} has no varying "
            f"{'bits' if args.bits else 'bytes'} in {path.name}.",
            file=sys.stderr,
        )
        return 1

    rows = []
    anchors_prepared = {label: prepare_series(s) for label, s in anchors.items()}
    for name, series in sorted(target.items()):
        best = _best_anchor(series, anchors_prepared, args.join_tol, args.min_n)
        rows.append((name, best))
    # Rank strongest anchor first; unanchored bytes last.
    rows.sort(key=lambda t: -(abs(t[1][1]) if t[1] else 0.0))

    if args.json:
        _json.dump(
            {
                "can_log": path.name,
                "id": target_label,
                "join_tol_s": args.join_tol,
                "bytes": [
                    {
                        "signal": name,
                        "anchor": best.label if best else None,
                        "r": best.r if best else None,
                        "n": best.n if best else 0,
                        "slope": best.slope if best else None,
                        "intercept": best.intercept if best else None,
                        "unit_guess": best.unit_guess if best else None,
                    }
                    for name, best in rows
                ],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    print(
        f"\n  {_BOLD}Investigate {target_label}{_RESET} "
        f"{_DIM}({path.name}, {len(target)} varying "
        f"{'bits' if args.bits else 'bytes'}, ≤{args.join_tol:g}s join){_RESET}"
    )
    shown = False
    for name, best in rows:
        if best and best.r is not None and abs(best.r) >= args.min_r:
            _label, r, n, m, c, unit = best
            rc = _GREEN if abs(r) >= 0.7 else _YELLOW
            fit = f" fit y={m:.4f}·x{c:+.2f}" if m is not None else ""
            unit_s = f" {_CYAN}{unit}{_RESET}" if unit else ""
            print(
                f"    {_BOLD}{name}{_RESET}  {rc}r={r:+.3f}{_RESET} vs {best.label} "
                f"{_DIM}n={n}{fit}{_RESET}{unit_s}"
            )
            shown = True
        else:
            print(f"    {_BOLD}{name}{_RESET}  {_DIM}no cross-ID anchor ≥ {args.min_r}{_RESET}")
    if not shown:
        print(
            f"    {_DIM}no byte cleared |r| ≥ {args.min_r} — lower --min-r, or the ID may "
            f"carry only counters/constants.{_RESET}"
        )
    print()
    return 0
