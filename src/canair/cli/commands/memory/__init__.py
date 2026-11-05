"""``canair memory`` — read ECU memory into local dumps, and work with them offline.

  * ``canair memory dump ECU START-END[,…]`` — read address ranges into a dump (live).
  * ``canair memory ls / show / diff / export`` — list, inspect, compare and export dumps
    (offline, :mod:`.views`).
  * ``canair memory set ECU`` — the ECU's ``memory:`` format, block and session (writes
    ``ecus/``).

Every read is ReadMemoryByAddress (``0x23``). Dumps are user state under the config
directory, never part of a profile: they can hold OEM software and secrets. Finding which
ranges answer is ``canair scan memory``.

Belongs: `canair memory`: the parsers, the confirmation and the text of dumps and diffs.
Not here: the reads (`live/memory/`), the dump store (`memory/`) and the ECU's `memory:`
editor (`yaml_edit/memory.py`).
Imports: `live`, `memory` and below.
"""

from .parser import NAME, add_parser, run_dump

__all__ = ["NAME", "add_parser", "run_dump"]
