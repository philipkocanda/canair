"""One-shot prompt to set the charging-grid region for the physical scan.

The physical-band scan's mains-voltage / line-frequency bands assume a region
(default: 230 V / 50 Hz EU). When the user hasn't set ``grid_region`` yet, offer
to set it — once — modelled on the conservative :mod:`canlib.first_run` pattern:
a TTY gets an interactive prompt, a piped run gets a single stderr note, and a
``grid_region_prompted`` sentinel ensures it never asks twice. Fully
best-effort: any failure falls through to the built-in defaults and never blocks
a scan.
"""

from __future__ import annotations

import sys

from .config import get_config_key, set_config_key
from .grid_regions import GRID_REGIONS

_SENTINEL_KEY = "grid_region_prompted"


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def resolve_grid_region(*, prompt: bool = True) -> str | None:
    """Return the configured ``grid_region``, prompting once if unset.

    If ``grid_region`` is set, return it. Otherwise, unless already prompted,
    ask (TTY) or emit a one-time note (piped) and record the sentinel so the
    prompt never repeats. Returns ``None`` when no region is chosen (the scan
    then uses the built-in EU-flavoured defaults).
    """
    try:
        region = get_config_key("grid_region")
        if region:
            return str(region)
        if not prompt or get_config_key(_SENTINEL_KEY):
            return None
        if _is_interactive():
            return _prompt_and_persist()
        _note_once()
        return None
    except Exception:
        # Never let a config/IO hiccup block a scan.
        return None


def _prompt_and_persist() -> str | None:
    choices = "/".join(GRID_REGIONS)
    print(
        "\n  No charging-grid region set — mains / line-frequency band detection\n"
        "  assumes 230 V / 50 Hz (EU). Set yours so the physical scan matches\n"
        "  your grid (it affects only mains/line-freq bands, not the vehicle).\n"
    )
    try:
        answer = input(f"  Region [{choices}/skip]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""

    upper = answer.upper()
    if upper in GRID_REGIONS:
        set_config_key("grid_region", upper)
        print(f"\n  ✓ grid_region set to '{upper}'. Change it: canair config set grid_region XX\n")
        return upper

    # Anything else (empty / "skip" / unrecognized) → record the sentinel so we
    # never ask again, and fall back to defaults for this run.
    set_config_key(_SENTINEL_KEY, True)
    print(
        "\n  Skipped — using EU (230 V / 50 Hz) defaults. Set one anytime:\n"
        "    canair config set grid_region US\n"
    )
    return None


def _note_once() -> None:
    set_config_key(_SENTINEL_KEY, True)
    print(
        "note: no grid_region set — physical-scan mains/line-freq bands assume "
        "230 V / 50 Hz (EU). Set yours with `canair config set grid_region "
        f"{GRID_REGIONS[0]}` (choices: {', '.join(GRID_REGIONS)}).",
        file=sys.stderr,
    )
