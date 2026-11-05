"""Human-readable report for ``canair captures uds --backfill-state-spans``."""

from __future__ import annotations

import sys
from typing import TextIO

from canair.render import ansi, glyphs
from canair.render.paint import Painter
from canair.states import join_states

_VERDICT_LABEL = {
    "spans": "timeline",
    "flat": "flat",
    "single": "single-state",
    "live": "live",
    "no-evidence": "no evidence",
}


def print_span_report(rows: list[dict], *, dry_run: bool, out: TextIO | None = None) -> None:
    """Print the per-session reconstruction table and a summary."""
    out = out or sys.stdout
    paint = Painter(out)
    raw = ansi.raw
    color = {
        "spans": raw.GREEN,
        "flat": raw.DIM,
        "single": raw.DIM,
        "live": "",
        "no-evidence": raw.YELLOW,
    }

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    # Only multi-state sessions are interesting: a single-state session has no
    # temporal ambiguity to resolve, and there are hundreds of them.
    actionable = [r for r in rows if r["verdict"] in ("spans", "flat", "live", "no-evidence")]

    print(f"  Analyzed {len(rows)} session(s); {len(actionable)} multi-state:\n", file=out)
    for r in actionable:
        verdict = _VERDICT_LABEL.get(r["verdict"], r["verdict"])
        mark = "*" if r["will_write"] else " "
        start = r["times"][0][:8] if r["times"] else "--:--:--"
        label = (r["label"] or "")[:30]
        print(
            f"  {mark} {r['date']} {paint(start, raw.DIM)} "
            f"{paint(f'{verdict:<12}', color.get(r['verdict'], ''))} "
            f"{r['n_spans']:>3} spans / {r['n_cycles']:>4} cycles  "
            f"rec={join_states(r['recorded'])}  {paint(label, raw.DIM)}",
            file=out,
        )
        if r["carried"] and r["n_spans"]:
            text = f"carried into every span (not placeable): {join_states(r['carried'])}"
            print(f"        {paint(text, raw.DIM)}", file=out)
        if r["verdict"] == "no-evidence" and r["n_untimed"]:
            text = f"{glyphs.WARN} {r['n_untimed']} untimed cycle(s)"
            print(f"        {paint(text, raw.YELLOW)}", file=out)

    print(file=out)
    print(
        "  " + "  ".join(f"{_VERDICT_LABEL.get(v, v)}={n}" for v, n in sorted(counts.items())),
        file=out,
    )
    n_write = sum(1 for r in rows if r["will_write"])
    if n_write:
        verb = "would write" if dry_run else "writing"
        print(f"  {n_write} session(s) to write (marked *); {verb}.", file=out)
    else:
        print("  No sessions to write.", file=out)
    if counts.get("live"):
        text = (
            f"({counts['live']} session(s) already carry live-observed "
            "spans; pass --overwrite to replace them)"
        )
        print(f"  {paint(text, raw.DIM)}", file=out)
