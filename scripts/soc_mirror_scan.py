#!/usr/bin/env python3
"""Search every co-polled PID byte for a mirror of the BMS SOC.

Answers "is SOC (state-of-charge) republished on another ECU?" — a question a
plain correlation can't settle during a *charge*, because SOC is monotone so
every rising byte (pack voltage, cumulative counters, warming temps) correlates
with it. This scan adds the discriminators that separate a real SOC value from a
monotone-charge coincidence:

  * scale        — d(SOC)/d(LSB); a true SOC field is ~0.5 %/LSB (raw = %*2) or
                   1.0 %/LSB, not ~0.01 (fine voltage) or ~2 (coarse temp).
  * coarseness   — SOC is coarse (tens of distinct values over a charge); a
                   voltage has hundreds.
  * value range  — an SOC field lands in 71..95 (%) or 142..190 (raw*2), etc.
  * charge-off   — SOC stays flat when charging stops; a voltage *collapses*.
                   Scan the charging-active window only so a collapsing voltage
                   doesn't get spuriously suppressed AND can't masquerade.

For every (ECU, PID) it co-polls, every byte and 16-bit word (BE/LE) of the
reassembled UDS payload (SID-first, PCI stripped) is time-aligned to the SOC
reference by nearest timestamp and linearly fit; the best hit per ECU/PID is
reported with the fingerprint columns so a human can judge SOC vs voltage/temp.

Reference SOC is read straight from the BMS 2101 payload (raw byte / 2) so the
scan is self-contained. BMS PIDs are excluded (SOC lives there by definition).

Usage:
    python3 scripts/soc_mirror_scan.py --date 2026-07-30 --state CHARGING
    python3 scripts/soc_mirror_scan.py --date 2026-07-30 --state CHARGING \
        --transport wican-ws --chg-end 14:36 --min-r 0.9

Notes:
  * A single steady charge proves a *negative* by scale/range/coarseness, not by
    correlation. To positively confirm/deny a mirror, re-run over a drive or
    discharge (non-monotone SOC with regen) so a real SOC field separates from
    voltage. Undersampled near-static blocks (keep-mode dedup) may have too few
    points — capture them with `--keep-all`.
  * Payload offsets are printed as ``pN`` (ISO-TP payload index, SID=p0), NOT
    WiCAN ``Bnn`` (which includes PCI bytes). Convert a hit with
    ``canair bix`` / ``canair decode … --dump-bytes`` before authoring a param.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from canlib.commands._captures_query import load_all_captures

# BMS 2101 reassembled-payload offset of the raw SOC byte (WiCAN B09); SOC% = raw/2.
_SOC_PAYLOAD_OFFSET = 6


def _tsec(t: str | None) -> float | None:
    """`HH:MM:SS[.fff]` -> seconds since midnight (all captures share one day)."""
    if not t:
        return None
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _payload(rec: dict) -> bytes:
    p = rec.get("payload")
    return bytes.fromhex(p) if p else b""


def _fit(xs: list[float], ys: list[float], min_n: int) -> tuple[float, float, float] | None:
    """Least-squares fit ys≈slope*xs+inter; returns (pearson_r, slope, inter)."""
    n = len(xs)
    if n < min_n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False))
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    r = sxy / (sxx * syy) ** 0.5
    slope = sxy / sxx
    return r, slope, my - slope * mx


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--date", required=True, help="capture date YYYY-MM-DD")
    ap.add_argument(
        "--state",
        default="CHARGING",
        help="vehicle_states token to select the session (default CHARGING)",
    )
    ap.add_argument("--transport", default=None, help="filter by transport (e.g. wican-ws)")
    ap.add_argument(
        "--chg-end",
        default=None,
        metavar="HH:MM",
        help="charging-active cutoff; ignore captures at/after this time (excludes the charge-off tail)",
    )
    ap.add_argument(
        "--tol", type=float, default=3.0, help="nearest-join tolerance seconds (default 3)"
    )
    ap.add_argument("--min-n", type=int, default=15, help="min aligned points to fit (default 15)")
    ap.add_argument(
        "--min-r",
        type=float,
        default=0.9,
        help="only report ECU/PIDs whose best |r| >= this (default 0.9)",
    )
    args = ap.parse_args(argv)

    cutoff = None
    if args.chg_end:
        h, m = args.chg_end.split(":")
        cutoff = int(h) * 3600 + int(m) * 60

    def in_window(t: float | None) -> bool:
        return t is not None and (cutoff is None or t < cutoff)

    rows = load_all_captures()
    sess = [
        r
        for r in rows
        if r.get("date") == args.date
        and args.state.upper() in {s.upper() for s in (r.get("vehicle_states") or [])}
        and (args.transport is None or r.get("transport") == args.transport)
    ]
    if not sess:
        print(
            f"no captures for date={args.date} state={args.state} transport={args.transport}",
            file=sys.stderr,
        )
        return 1

    soc = []
    for r in sess:
        if r.get("ecu") == "BMS" and str(r.get("pid")) == "2101":
            b = _payload(r)
            t = _tsec(r.get("time"))
            if len(b) > _SOC_PAYLOAD_OFFSET and in_window(t):
                soc.append((t, b[_SOC_PAYLOAD_OFFSET] / 2))
    soc.sort()
    if len(soc) < args.min_n:
        print(f"insufficient SOC reference points ({len(soc)})", file=sys.stderr)
        return 1

    def near(t: float) -> float | None:
        best, bd = None, args.tol
        for st, sv in soc:
            d = abs(st - t)
            if d < bd:
                bd, best = d, sv
        return best

    groups: dict[tuple, list] = defaultdict(list)
    for r in sess:
        if r.get("ecu") == "BMS":
            continue  # SOC lives on BMS by definition
        if in_window(_tsec(r.get("time"))):
            groups[(r.get("ecu"), str(r.get("pid")))].append(r)

    best: dict[tuple, tuple] = {}
    for (ecu, pid), rs in groups.items():
        series = [(_tsec(r.get("time")), _payload(r)) for r in rs if r.get("time") and _payload(r)]
        series = [(t, b) for t, b in series if t is not None]
        if len(series) < 20:
            continue
        maxlen = min(len(b) for _, b in series)
        for i in range(2, maxlen):  # skip SID + PID/DID echo
            for width, label, be in (
                (1, f"p{i}", True),
                (2, f"p{i}:{i + 1}BE", True),
                (2, f"p{i}:{i + 1}LE", False),
            ):
                if i + width > maxlen:
                    continue
                xs, ys = [], []
                for t, b in series:
                    sv = near(t)
                    if sv is None:
                        continue
                    v = (
                        b[i]
                        if width == 1
                        else ((b[i] << 8 | b[i + 1]) if be else (b[i + 1] << 8 | b[i]))
                    )
                    xs.append(v)
                    ys.append(sv)
                f = _fit(xs, ys, args.min_n)
                if not f:
                    continue
                r, slope, _ = f
                if abs(r) > best.get((ecu, pid), (0,))[0]:
                    best[(ecu, pid)] = (
                        abs(r),
                        round(r, 3),
                        label,
                        round(slope, 4),
                        len(set(xs)),
                        min(xs),
                        max(xs),
                    )

    print(
        f"SOC-mirror scan  date={args.date} state={args.state}"
        + (f" transport={args.transport}" if args.transport else "")
        + (f" chg-end={args.chg_end}" if args.chg_end else "")
    )
    print(f"SOC reference: {soc[0][1]}..{soc[-1][1]} %  (n={len(soc)})")
    print(
        "\nA real SOC field: slope≈0.5 or 1.0 %/LSB, coarse (tens distinct), range in 71-95 or 142-190."
    )
    print("Fine (100s distinct, slope~0.01) = voltage; coarse slope~2 wrong-range = temperature.\n")
    print(
        f"{'ECU':5} {'PID':8} {'best_r':7} {'byte':10} {'d%/LSB':9} {'dist':5} {'min':7} {'max':7}"
    )
    printed = 0
    for (ecu, pid), v in sorted(best.items(), key=lambda kv: -kv[1][0]):
        _, r, label, slope, dist, vmin, vmax = v
        if abs(r) < args.min_r:
            continue
        print(f"{ecu:5} {pid:8} {r:+.3f}  {label:10} {slope:9} {dist:5} {vmin:7} {vmax:7}")
        printed += 1
    if not printed:
        print(f"(no ECU/PID with best |r| >= {args.min_r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
