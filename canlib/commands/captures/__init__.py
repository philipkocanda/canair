"""Query captured data — the ``captures`` command group.

Two domains behind one command, split by *kind*:

* ``uds``  — diagnostic UDS payloads in ``captures/*.json`` (domain A): the
  QUERY/diff/step/summary/sessions/latest surface and the mutating modes.
* ``can``  — imported raw broadcast-CAN frame logs (domain B), listed from
  ``captures/can/index.yaml``.

plus the store-maintenance kinds (``migrate``, ``migrate-rx``) and the git
``merge-driver``. A bare ``canair captures BMS 2102`` is shorthand for the ``uds``
kind, injected by ``cli.py``'s ``_GROUP_DEFAULTS``.

This module is only the group: it wires the kinds together and re-exports the
handful of names other commands consume. Each kind and view lives in its own
submodule — ``uds`` (parser + orchestration), ``listing``/``sessions``/``diff``
(views), ``delete``/``backfill``/``set_state`` (QUERY-driven mutating modes),
``maint`` (whole-store operations), ``can``, and the shared ``query`` data layer.
"""

import argparse

from canlib.commands._group import group_help

from .maint import orphan_notice
from .query import build_query, load_all_captures

NAME = "captures"
ALIASES = ["cap"]

__all__ = [
    "ALIASES",
    "NAME",
    "add_parser",
    "build_query",
    "load_all_captures",
    "orphan_notice",
]


def add_parser(subparsers) -> argparse.ArgumentParser:
    from . import merge_driver
    from .can import _add_can_parser
    from .maint import _add_migrate_parser, _add_migrate_rx_parser
    from .uds import _add_uds_parser

    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Query captured data: uds (diagnostic payloads) | can (raw frame logs)",
        description="Query captured data. Choose a kind:\n"
        "  uds   diagnostic UDS payloads (captures/*.json) — the QUERY/diff/step/\n"
        "        summary/sessions/latest/recover surface (domain A)\n"
        "  can   imported raw broadcast-CAN frame logs (captures/can/index.yaml,\n"
        "        domain B)\n\n"
        "A bare `canair captures BMS 2102` (or any of the --summary/--sessions/… "
        "flags) is shorthand for `canair captures uds …`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kinds = parser.add_subparsers(dest="captures_kind", metavar="<kind>")
    _add_uds_parser(kinds)
    _add_can_parser(kinds)
    _add_migrate_parser(kinds)
    _add_migrate_rx_parser(kinds)
    merge_driver.add_parser(kinds)
    parser.set_defaults(func=group_help("_captures_group_parser"), _captures_group_parser=parser)
    return parser
