"""Human-readable report for ``canair captures uds --backfill-states``."""

from __future__ import annotations

import sys
from typing import TextIO

from canair.render import ansi, glyphs
from canair.render.paint import Painter
from canair.states import join_states

_VERDICT_LABEL = {
    "fill": "fill",
    "agree": "agree",
    "extra": "extra",
    "conflict": "conflict",
    "undetermined": "undetermined",
}


def print_report(
    rows: list[dict], *, overwrite: bool, dry_run: bool, out: TextIO | None = None
) -> None:
    """Print the per-session inference table and a summary."""
    out = out or sys.stdout
    paint = Painter(out)
    raw = ansi.raw
    verdict_color = {
        "fill": raw.GREEN,
        "agree": raw.DIM,
        "extra": "",
        "conflict": raw.YELLOW,
        "undetermined": raw.DIM,
    }

    counts: dict[str, int] = {}
    n_write = 0
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        if r["will_write"]:
            n_write += 1

    # Only actionable verdicts are listed; agree/undetermined fold into the
    # summary so a large capture set doesn't flood the terminal.
    actionable = [r for r in rows if r["verdict"] in ("fill", "extra", "conflict")]

    print(f"  Analyzed {len(rows)} session(s); {len(actionable)} actionable:\n", file=out)
    for r in actionable:
        verdict = _VERDICT_LABEL.get(r["verdict"], r["verdict"])
        mark = "*" if r["will_write"] else " "
        span = r["times"][0][:8] if r["times"] else "--:--:--"
        recorded = join_states(r["recorded"]) or "(none)"
        inferred = join_states(r["inferred"]) or "(none)"
        label = (r["label"] or "")[:36]
        print(
            f"  {mark} {r['date']} {paint(span, raw.DIM)} "
            f"{paint(f'{verdict:<12}', verdict_color.get(r['verdict'], ''))} "
            f"rec={recorded}  inf={inferred}  {paint(label, raw.DIM)}",
            file=out,
        )
        if r["verdict"] == "conflict":
            false = join_states(r["definitely_false"])
            text = f"{glyphs.WARN} recorded state contradicts evidence (provably false: {false})"
            print(f"        {paint(text, raw.YELLOW)}", file=out)
        if r["will_write"]:
            text = f"{glyphs.ARROW} will set: {join_states(r['new_states'])}"
            print(f"        {paint(text, raw.GREEN)}", file=out)

    print(file=out)
    print(
        "  " + "  ".join(f"{_VERDICT_LABEL.get(v, v)}={n}" for v, n in sorted(counts.items())),
        file=out,
    )
    if n_write:
        verb = "would write" if dry_run else "writing"
        print(f"  {n_write} session(s) to write (marked *); {verb}.", file=out)
    else:
        print("  No sessions to write.", file=out)
    if not overwrite and counts.get("conflict"):
        text = f"({counts['conflict']} conflict(s) not written; pass --overwrite to correct them)"
        print(f"  {paint(text, raw.DIM)}", file=out)
