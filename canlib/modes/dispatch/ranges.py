"""Parsing the hex PID/DID ranges the scan surfaces take.

Its own module because it is the one piece of the dispatcher with no async, no
terminal and no modes — and because it raises ``argparse.ArgumentTypeError``,
which is the reason it did not move further down into the library when the
dispatcher did.

Note ``canlib.modes.iocontrol_scan._parse_range_str`` parses the same
``START-END`` form but returns ``None`` instead of raising. Unifying them is a
behaviour change (their error contracts differ), so it is deliberately not done
here.
"""

from __future__ import annotations

import argparse
import re


def parse_range(range_str: str) -> tuple[int, int]:
    """Parse a PID/DID range like '01-FF', 'E000-E0FF', or 'BC01-BC0B'."""
    match = re.match(r"^([0-9A-Fa-f]+)-([0-9A-Fa-f]+)$", range_str)
    if not match:
        raise argparse.ArgumentTypeError(
            f"Invalid range: {range_str}. Expected format: 01-FF or E000-E0FF"
        )
    return int(match.group(1), 16), int(match.group(2), 16)
