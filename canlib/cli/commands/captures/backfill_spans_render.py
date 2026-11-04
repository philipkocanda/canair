"""Human-readable report for ``canair captures uds --backfill-state-spans``."""

from __future__ import annotations

import sys

from canlib.states import join_states

_VERDICT_LABEL = {
    "spans": "timeline",
    "flat": "flat",
    "single": "single-state",
    "live": "live",
    "no-evidence": "no evidence",
}


def _colors() -> dict[str, str]:
    if not sys.stdout.isatty():
        return dict.fromkeys(("dim", "green", "yellow", "cyan", "reset"), "")
    return {
        "dim": "\033[2m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "cyan": "\033[96m",
        "reset": "\033[0m",
    }


def print_span_report(rows: list[dict], *, dry_run: bool) -> None:
    """Print the per-session reconstruction table and a summary."""
    c = _colors()
    color = {
        "spans": c["green"],
        "flat": c["dim"],
        "single": c["dim"],
        "live": c["cyan"],
        "no-evidence": c["yellow"],
    }

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    # Only multi-state sessions are interesting: a single-state session has no
    # temporal ambiguity to resolve, and there are hundreds of them.
    actionable = [r for r in rows if r["verdict"] in ("spans", "flat", "live", "no-evidence")]

    print(f"  Analyzed {len(rows)} session(s); {len(actionable)} multi-state:\n")
    for r in actionable:
        verdict = _VERDICT_LABEL.get(r["verdict"], r["verdict"])
        mark = "*" if r["will_write"] else " "
        start = r["times"][0][:8] if r["times"] else "--:--:--"
        label = (r["label"] or "")[:30]
        print(
            f"  {mark} {r['date']} {c['dim']}{start}{c['reset']} "
            f"{color.get(r['verdict'], '')}{verdict:<12}{c['reset']} "
            f"{r['n_spans']:>3} spans / {r['n_cycles']:>4} cycles  "
            f"rec={join_states(r['recorded'])}  {c['dim']}{label}{c['reset']}"
        )
        if r["carried"] and r["n_spans"]:
            print(
                f"        {c['dim']}carried into every span (not placeable): "
                f"{join_states(r['carried'])}{c['reset']}"
            )
        if r["verdict"] == "no-evidence" and r["n_untimed"]:
            print(f"        {c['yellow']}{r['n_untimed']} untimed cycle(s){c['reset']}")

    print()
    print("  " + "  ".join(f"{_VERDICT_LABEL.get(v, v)}={n}" for v, n in sorted(counts.items())))
    n_write = sum(1 for r in rows if r["will_write"])
    if n_write:
        verb = "would write" if dry_run else "writing"
        print(f"  {n_write} session(s) to write (marked *); {verb}.")
    else:
        print("  No sessions to write.")
    if counts.get("live"):
        print(
            f"  {c['dim']}({counts['live']} session(s) already carry live-observed "
            f"spans; pass --overwrite to replace them){c['reset']}"
        )
