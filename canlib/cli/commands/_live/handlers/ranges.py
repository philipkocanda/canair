"""Parsing the hex PID/DID ranges the scan surfaces take.

Its own module because it has no async, no terminal and no modes, and because it raises
``argparse.ArgumentTypeError``, which keeps it in the command layer.

"""

from __future__ import annotations

import argparse

from canlib.core.id_ranges import IdRange, parse_id_range, parse_id_ranges


def parse_range(range_str: str) -> IdRange:
    """Parse a PID/DID range like '01-FF', 'E000-E0FF', or 'BC01-BC0B'."""
    try:
        return parse_id_range(range_str)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"Invalid range: {range_str}. Expected format: 01-FF or E000-E0FF ({e})"
        ) from e


def parse_ranges(range_str: str) -> tuple[IdRange, ...]:
    """Parse one range or a comma list (``B400-B41F,B100-B11F``)."""
    try:
        return parse_id_ranges(range_str)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid range: {range_str} ({e})") from e
