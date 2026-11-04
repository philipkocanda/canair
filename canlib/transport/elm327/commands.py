"""The ELM327 command facts the adapter-facing modules share.

How a command is compared (upper-cased, whitespace removed, so ``at z`` is ``ATZ``) and
which commands reset the whole adapter. One home, so the terminal, the addressing cache
and the EEPROM guard cannot disagree about either. A dependency-free leaf.
"""

from __future__ import annotations

from typing import Final

# Commands that put the adapter back to its defaults, memory function included.
RESET_COMMANDS: Final = frozenset({"ATZ", "ATWS", "ATD"})


def canonical(command: str) -> str:
    """An adapter command upper-cased without whitespace, the form it is compared in."""
    return "".join(command.split()).upper()
