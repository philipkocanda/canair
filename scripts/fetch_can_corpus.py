#!/usr/bin/env python3
"""Fetch third-party raw-CAN corpora for local RE/testing (fetch-on-demand).

Downloads reference broadcast-CAN logs into a **gitignored** ``references/can/``
so they are never committed to this (public) repo — the upstream repo ships no
license, so we don't redistribute its data; we fetch it on demand + attribute it.
Only tiny hand-trimmed slices live in ``tests/fixtures/can/`` for unit tests.

This is the **unlicensed / no-clear-license** arm of the raw-CAN log policy
(docs/concepts/broadcast-frames.md → "Storing raw-CAN logs"): third-party logs
are committed only when their license permits redistribution; unlicensed ones
stay fetch-on-demand here. Our own captured logs are committed fully (via Git
LFS when large).

Current corpus: the near-identical Hyundai **Ioniq 28 kWh** internal-bus logs
from https://github.com/uhi22/Ioniq28Investigations (a WiCAN-less PCAN tap) — the
drive-mode/regen/thermal broadcast data the OBD port can't see. The CSVs are
SavvyCAN **GVRET** format, importable with ``canair import can``.

Usage:
    python3 scripts/fetch_can_corpus.py            # fetch all
    python3 scripts/fetch_can_corpus.py --list     # show what would be fetched
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST_DIR = REPO_ROOT / "references" / "can"

_BASE = "https://raw.githubusercontent.com/uhi22/Ioniq28Investigations/main/CAN/"
CORPUS: dict[str, str] = {
    # DBC signal definitions (Stage-4 `import dbc` input)
    "hyundai_Ioniq28Motor.dbc": _BASE + "hyundai_Ioniq28Motor.dbc",
    # GVRET frame logs (Stage-3 `import can --format gvret` input)
    "IONIQ_PCAN_drive_fwd_neutral_drive_reverse_neutral.csv": _BASE
    + "IONIQ_PCAN_drive_fwd_neutral_drive_reverse_neutral.csv",
    "IONIQ_PCAN_leave_car_while_drive_ready_beep.csv": _BASE
    + "IONIQ_PCAN_leave_car_while_drive_ready_beep.csv",
    # NOTE: EPCU_torquePro.csv (~38 MB) is intentionally omitted from the default
    # fetch (large); add it manually if needed.
}

_ATTRIBUTION = (
    "Source: https://github.com/uhi22/Ioniq28Investigations (Hyundai Ioniq 28 kWh "
    "internal-bus logs). Fetched locally; not redistributed by canair."
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--list", action="store_true", help="List files without downloading")
    ap.add_argument("--force", action="store_true", help="Re-download files that already exist")
    args = ap.parse_args(argv)

    if args.list:
        for name, url in CORPUS.items():
            print(f"{name}\t{url}")
        print(f"\nDestination: {DEST_DIR} (gitignored)\n{_ATTRIBUTION}")
        return 0

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in CORPUS.items():
        dest = DEST_DIR / name
        if dest.exists() and not args.force:
            print(f"= exists  {name} (use --force to re-download)")
            continue
        print(f"↓ fetching {name} …")
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception as e:  # network/HTTP errors
            print(f"  ! failed: {e}", file=sys.stderr)
            return 1
        print(f"  → {dest}")
    print(f"\n{_ATTRIBUTION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
