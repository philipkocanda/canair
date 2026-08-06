"""``correlate can`` — the domain-B kind, over a raw broadcast-CAN frame log.

Builds per-byte (or per-bit) series straight from a native frame log's arbitration
IDs and runs them through the same ranked/``--against``/cluster core as the
diagnostic kind. Labels render as raw-CAN ``0xID:rN`` — no WiCAN indices, no PCI.
"""

from __future__ import annotations

import json as _json
import sys

from canlib.align import (
    join_prepared,
    prepare_series,
)
from canlib.xanalysis import (
    colinear_clusters,
    correlate_matrix,
    correlation,
)

from .promote import _warn_thin_reference_join
from .render import (
    _color_r,
    _print_can_mirrors,
)

NAME = "correlate"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def _run_can_log(args) -> int:
    """Correlate a raw broadcast-CAN frame log's per-byte (and --bits) series.

    Domain B: reads the native frame log into ``0xID:rN`` series and runs the
    *same* correlate core as the diagnostic path — ranked cross-ID pairs by
    default, every byte vs an ``--against 0xID:rN`` reference, or (with
    ``--find-mirrors``) byte/bit positions time-aligned equal across IDs. Series
    sharing an arbitration ID are intra (dropped unless --include-intra), so
    cross-ID relationships surface first.
    """
    from pathlib import Path

    from canlib import frame_series
    from canlib.can_logs import CanLogError

    path = Path(args.file)
    if not path.is_file():
        print(f"correlate can: no such file: {path}", file=sys.stderr)
        return 1
    try:
        id_filter = frame_series.parse_id_filter(args.id)
        if args.bits:
            series = frame_series.build_frame_bit_series(path, args.can_format, id_filter=id_filter)
        else:
            series = frame_series.build_frame_series(
                path, args.can_format, id_filter=id_filter, min_distinct=4
            )
    except (ValueError, CanLogError) as e:
        print(f"correlate can: {e}", file=sys.stderr)
        return 1
    if not series:
        print("No varying frame bytes found in scope.", file=sys.stderr)
        return 1

    src = f"{path.name} ({len(series)} varying {'bits' if args.bits else 'bytes'})"

    if args.find_mirrors:
        return _print_can_mirrors(series, path, args)

    # --against: rank every series vs one reference label present in the log.
    if args.against:
        ref = series.get(args.against)
        if ref is None:
            avail = ", ".join(sorted(series)[:6])
            print(
                f"--against: {args.against!r} is not a varying byte in {path.name}. "
                f"Available e.g.: {avail} …",
                file=sys.stderr,
            )
            return 1
        rows = []
        best_n = 0
        ref_prepared = prepare_series(ref)
        for name, s in series.items():
            if name == args.against:
                continue
            xs, ys, n = join_prepared(ref_prepared, prepare_series(s), tol_s=args.join_tol)
            best_n = max(best_n, n)
            if n < args.min_n:
                continue
            r = correlation(xs, ys, args.method)
            if r is None or abs(r) < args.min_r:
                continue
            rows.append((name, r, n))
        _warn_thin_reference_join("correlate", args.against, best_n, len(ref), args)
        rows.sort(key=lambda t: -abs(t[1]))
        rows = rows[: args.top]
        if args.json:
            _json.dump(
                {
                    "can_log": path.name,
                    "against": args.against,
                    "hits": [{"signal": n, "r": r, "n": nn} for n, r, nn in rows],
                },
                sys.stdout,
                indent=2,
            )
            print()
            return 0
        print(
            f"\n  {_BOLD}vs {args.against}{_RESET} "
            f"{_DIM}({src}, nearest-join ≤{args.join_tol:g}s){_RESET}"
        )
        if not rows:
            print(f"    {_DIM}no byte with |r| ≥ {args.min_r} (n ≥ {args.min_n}){_RESET}\n")
            return 0
        for name, r, n in rows:
            print(f"    {_color_r(r)}  {name}  {_DIM}n={n}{_RESET}")
        print()
        return 0

    # Default: ranked cross-ID pairs.
    hits = correlate_matrix(
        series,
        tol_s=args.join_tol,
        min_r=args.min_r,
        min_n=args.min_n,
        include_intra=args.include_intra,
        method=args.method,
    )
    if args.json:
        _json.dump(
            {
                "can_log": path.name,
                "hits": [{"a": h.a, "b": h.b, "r": h.r, "n": h.n} for h in hits[: args.top]],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0
    if not hits:
        print(f"No cross-ID frame-byte correlations with |r| ≥ {args.min_r} (n ≥ {args.min_n}).")
        return 0
    clusters = [] if args.no_cluster else colinear_clusters(hits)
    clustered = {sig for c in clusters for sig in c}
    remaining = [
        h
        for h in hits
        if not (
            h.a in clustered and h.b in clustered and any(h.a in c and h.b in c for c in clusters)
        )
    ][: args.top]
    print(
        f"\n  {_BOLD}Frame-byte correlations{_RESET} "
        f"{_DIM}({src}, |r|≥{args.min_r}, n≥{args.min_n}){_RESET}"
    )
    for c in sorted(clusters, key=len, reverse=True):
        members = sorted(c)
        shown = ", ".join(members[:4]) + (f", +{len(members) - 4} more" if len(members) > 4 else "")
        print(f"    {_GREEN}≈ cluster{_RESET} {_DIM}({len(members)} signals){_RESET}  {shown}")
    for h in remaining:
        print(f"    {_color_r(h.r)}  {h.a}  {_DIM}⟷{_RESET}  {h.b}  {_DIM}n={h.n}{_RESET}")
    print()
    return 0
