"""Human-readable report for ``canair captures uds --backfill-states``."""

from __future__ import annotations

import sys

from canlib.states import join_states

_VERDICT_LABEL = {
    "fill": "fill",
    "agree": "agree",
    "extra": "extra",
    "conflict": "conflict",
    "undetermined": "undetermined",
}


def _colors() -> dict[str, str]:
    if not sys.stdout.isatty():
        return dict.fromkeys(("dim", "green", "yellow", "red", "cyan", "reset"), "")
    return {
        "dim": "\033[2m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "red": "\033[91m",
        "cyan": "\033[96m",
        "reset": "\033[0m",
    }


def print_report(rows: list[dict], *, overwrite: bool, dry_run: bool) -> None:
    """Print the per-session inference table and a summary."""
    c = _colors()
    verdict_color = {
        "fill": c["green"],
        "agree": c["dim"],
        "extra": c["cyan"],
        "conflict": c["red"],
        "undetermined": c["dim"],
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

    print(f"  Analyzed {len(rows)} session(s); {len(actionable)} actionable:\n")
    for r in actionable:
        vcol = verdict_color.get(r["verdict"], "")
        verdict = _VERDICT_LABEL.get(r["verdict"], r["verdict"])
        mark = "*" if r["will_write"] else " "
        span = r["times"][0][:8] if r["times"] else "--:--:--"
        recorded = join_states(r["recorded"]) or "(none)"
        inferred = join_states(r["inferred"]) or "(none)"
        label = (r["label"] or "")[:36]
        print(
            f"  {mark} {r['date']} {c['dim']}{span}{c['reset']} "
            f"{vcol}{verdict:<12}{c['reset']} "
            f"rec={recorded}  inf={inferred}  {c['dim']}{label}{c['reset']}"
        )
        if r["verdict"] == "conflict":
            false = join_states(r["definitely_false"])
            print(
                f"        {c['red']}recorded state contradicts evidence "
                f"(provably false: {false}){c['reset']}"
            )
        if r["will_write"]:
            print(
                f"        {c['green']}\u2192 will set: {join_states(r['new_states'])}{c['reset']}"
            )

    print()
    summary = "  " + "  ".join(f"{_VERDICT_LABEL.get(v, v)}={n}" for v, n in sorted(counts.items()))
    print(summary)
    if n_write:
        verb = "would write" if dry_run else "writing"
        print(f"  {n_write} session(s) to write (marked *); {verb}.")
    else:
        print("  No sessions to write.")
    if not overwrite and counts.get("conflict"):
        print(
            f"  {c['dim']}({counts['conflict']} conflict(s) not written; "
            f"pass --overwrite to correct them){c['reset']}"
        )
