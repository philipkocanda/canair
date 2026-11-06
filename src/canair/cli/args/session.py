"""``--wake``, declared once for every command that opens a diagnostic session."""

from __future__ import annotations

import argparse

WAKE_HELP = "Wake ECUs from deep sleep first (the ECU's wake: ritual, else 10 01)"


def add_wake_arg(parser: argparse.ArgumentParser | argparse._ArgumentGroup) -> None:
    """Add ``--wake`` (``args.wake``), with the one help text every command shares."""
    parser.add_argument("--wake", action="store_true", help=WAKE_HELP)
