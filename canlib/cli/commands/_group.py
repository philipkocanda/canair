"""Shared scaffolding for command *groups* (subcommand-of-subcommand dispatch).

Several top-level commands (``scan``, ``correlate``, ``hunt``, ``investigate``,
``ecu``, ``captures``, ``wican``) are themselves groups: they take a *kind* /
*action* sub-subcommand and, when invoked with none, should print their own
help and exit non-zero. This module centralizes that fallback so each command
doesn't hand-roll an identical ``_group_help``.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable


def group_help(parser_attr: str) -> Callable[[argparse.Namespace], int]:
    """Build a ``func`` fallback that prints ``parser_attr``'s help, returns 1.

    Register with e.g.::

        parser.set_defaults(func=group_help("_scan_group_parser"),
                            _scan_group_parser=parser)

    so a bare ``canair scan`` (no kind) prints the group's help. The parser is
    read from ``args`` at call time (stored under ``parser_attr``) rather than
    captured, to avoid a reference cycle in the defaults.
    """

    def _fallback(args: argparse.Namespace) -> int:
        parser = getattr(args, parser_attr, None)
        if parser is not None:
            parser.print_help()
        return 1

    return _fallback
