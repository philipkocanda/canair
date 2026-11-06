"""Query captured data — the ``captures`` command group.

Two domains behind one command, split by *kind*:

* ``uds``  — diagnostic UDS payloads in ``captures/*.json`` (domain A): the
  QUERY/diff/step/summary/sessions/latest surface and the mutating modes.
* ``can``  — imported raw broadcast-CAN frame logs (domain B), listed from
  ``captures/can/index.yaml``.

plus the store-maintenance kinds (``migrate``, ``migrate-rx``,
``migrate-discover``, ``split-batched``, ``compact``, ``purge-sensitive``,
``migrate-variants``) and the git
``merge-driver``. A bare
``canair captures BMS 2102`` is shorthand for the ``uds`` kind, injected by
``cli.py``'s ``_GROUP_DEFAULTS``.

Rules the views and modes keep:

* The default list view is capped at ``--limit`` (50) with a loud footer for the hidden history,
  so a bare query cannot flood a terminal or an agent's context.
* A mutating mode never touches a base layer of a layered profile (``session_gate.refuse_write``),
  and ``--delete`` refuses to run bare. ``--set-state``, ``--set-notes``, ``--set-transport`` and
  ``--set-variant`` require a scope filter, so none can relabel the whole history by accident.
* ``--backfill-states`` fills unset states and only reports contradicted ones unless
  ``--overwrite``. ``--dry-run`` previews every mutation.

This module is only the group: it wires the kinds together and re-exports the
handful of names other commands consume. Each kind and view lives in its own
submodule — ``uds`` (parser + orchestration), ``listing``/``sessions``/``diff``
(views), ``delete``/``backfill``/``set_state`` (QUERY-driven mutating modes),
``maint`` (whole-store operations), ``can``, and the shared ``query`` data layer.

Belongs: `canair captures`: querying, stepping, diffing and maintaining recorded captures.
Not here: the store itself (`captures/`) and the analysis behind a view (`analysis/`).
Imports: `captures`, `analysis`, `states`, `ecus` and below.
"""

import argparse

from canair.cli.args.group import group_help

from . import compact, merge_driver, migrate_variants, purge_sensitive
from .can import _add_can_parser
from .maint import (
    _add_migrate_discover_parser,
    _add_migrate_parser,
    _add_migrate_rx_parser,
    _add_split_batched_parser,
    orphan_notice,
)
from .query import build_query
from .uds import _add_uds_parser

NAME = "captures"
ALIASES = ["cap"]

__all__ = [
    "ALIASES",
    "NAME",
    "add_parser",
    "build_query",
    "orphan_notice",
]


def add_parser(subparsers) -> argparse.ArgumentParser:
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
    _add_migrate_discover_parser(kinds)
    _add_split_batched_parser(kinds)
    compact.add_parser(kinds)
    purge_sensitive.add_parser(kinds)
    migrate_variants.add_parser(kinds)
    merge_driver.add_parser(kinds)
    parser.set_defaults(func=group_help("_captures_group_parser"), _captures_group_parser=parser)
    return parser
