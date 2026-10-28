"""Argparse surface for ``canair search`` — the group and its ``uds`` kind."""

from __future__ import annotations

import argparse

from canlib.capture_dates import add_scope_args
from canlib.commands._group import group_help
from canlib.uds.notation import add_notation_arg

from .needle import DEFAULT_MIN_LEN, IN_ALL, IN_CHOICES
from .uds import run

NAME = "search"

_EPILOG = """\
examples:
  # which ECU/PID holds this part number (or any substring of it)?
  canair search 95400G7470

  # list every plausible string in the captures: part numbers, versions, ECU names
  canair search

  # a regular expression, case-sensitive
  canair search --regex 'G[0-9]{4}' --case-sensitive

  # anything VIN-shaped, in payloads and in labels/notes
  canair search --vin

  # a byte pattern, wherever it occurs in any payload
  canair search --hex 'DE AD BE EF' --query BMS

  # include the decoded identity fields in ecus/, and emit machine-readable output
  canair search KMHC --defs --json

  # a PATTERN that is also a kind name (or starts with '-') goes after `--`
  canair search -- uds

Each payload hit carries a paste-ready `pids upsert-param ... --type ascii` command, so
a string you found can be defined as a signal in one step.
"""


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Find a string, VIN or byte pattern anywhere in the captures",
        description=(
            "Find a string, VIN or byte pattern anywhere in the captures — without already\n"
            "knowing which ECU and PID holds it.\n\n"
            "Choose a domain kind:\n"
            "  uds   diagnostic captures (default) — payload bytes, labels, notes\n"
            "        (domain A). A bare `canair search …` is shorthand for this.\n\n"
            "With a PATTERN it finds that needle; with none it inventories every\n"
            "plausible string (part numbers, versions, ECU names). Read-only: searches\n"
            "captures/ (and, with --defs, the ecus/ identity fields), never talks to the\n"
            "device.\n\n"
            "A first word that names a kind is taken as the kind, so to search for the\n"
            "text 'uds' itself, end the options first: `canair search -- uds`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    kinds = parser.add_subparsers(dest="search_kind", metavar="<kind>")
    _add_uds_parser(kinds)
    parser.set_defaults(func=group_help("_search_group_parser"), _search_group_parser=parser)
    return parser


def _add_uds_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "uds",
        help="Search diagnostic capture payloads, labels and notes (domain A)",
        description=(
            "Search every captured payload (and, by default, labels and notes) for a "
            "string, VIN or byte pattern; or, with no PATTERN, list every plausible "
            "string. Findings are grouped by location (ECU, PID, offset), not by "
            "capture, with a hit count and first/last-seen date."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "pattern",
        nargs="?",
        metavar="PATTERN",
        help="Literal substring, case-insensitive (omit to list every plausible string)",
    )

    match = parser.add_argument_group("match")
    match.add_argument("--regex", action="store_true", help="Treat PATTERN as a regular expression")
    match.add_argument(
        "--hex",
        metavar="BYTES",
        help="Search payloads for a byte pattern instead of text (e.g. 'DE AD BE EF')",
    )
    match.add_argument(
        "--vin",
        action="store_true",
        help="Shortcut for anything VIN-shaped (17 characters, ISO 3779, at least one letter)",
    )
    match.add_argument(
        "--case-sensitive", action="store_true", help="Make a text PATTERN honour case"
    )

    hay = parser.add_argument_group("haystack")
    hay.add_argument(
        "--in",
        dest="in_",
        choices=IN_CHOICES,
        default=IN_ALL,
        help="Where to look: payload bytes (including the raw responses scan notes "
        "record), free text (labels, notes, text responses), or all (default). Note that "
        "--label NARROWS to matching sessions, whereas a PATTERN searches (labels "
        "included, by default)",
    )
    hay.add_argument(
        "--defs",
        action="store_true",
        help="Also search the ecus/ identity fields (part numbers, serials, versions)",
    )
    hay.add_argument(
        "--query",
        metavar="QUERY",
        help="Narrow to ECU/PID selectors in the shared mini-language "
        "(e.g. 'BMS', 'BCM:22F188', 'VCU BMS')",
    )

    inv = parser.add_argument_group("inventory (no PATTERN)")
    inv.add_argument(
        "--min-len",
        type=int,
        default=None,
        metavar="N",
        help=f"Minimum printable run length (default {DEFAULT_MIN_LEN})",
    )
    inv.add_argument(
        "--all-runs",
        action="store_true",
        help="Disable the plausibility filter (raw strings(1) behaviour — mostly noise: "
        "numeric telemetry bytes are often printable)",
    )

    out = parser.add_argument_group("output")
    out.add_argument(
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="Show at most N findings (default 50; 0 = no cap). A footer reports any hidden",
    )
    out.add_argument("--json", action="store_true", help="Machine-readable output")
    add_notation_arg(parser)
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser
