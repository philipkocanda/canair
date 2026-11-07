"""Validate a profile's ecus/, profile.yaml, and captures/ against their schemas.

Validators merged into one subcommand:

  * ``validate pids`` — per-ECU definition files in ecus/ vs pids_schema.yaml (the ECU schema)
    (also validates profile.yaml). ``validate ecus`` is an alias.
  * ``validate captures`` — capture files in captures/ vs captures_schema.json
  * ``validate states`` — vehicle_states.yaml (power-state vocabulary + predicates)
  * ``validate can-buses`` — can_buses.yaml (per-profile CAN bus vocabulary)
  * ``validate signals`` — signals/ broadcast signal definitions (domain B)
  * ``validate groups`` — groups.yaml (named capture/monitor selector groups)
  * ``validate actions`` — actions.yaml (guarded profile actions)
  * ``validate variants`` — variants.yaml (the car's variants and their metadata)
  * ``validate can`` — captures/can/index.yaml (raw-CAN log index)
  * ``validate all`` (default) — run all of them

The checks are library code in :mod:`canair.validation`, which the profile editors also call as
their post-write guard. They return data and never print. This package prints it and picks the
exit code: the parser and ``run`` here, and one runner for each target (``files``, ``pids``,
``captures``).

Belongs: `canair validate`: the parser, and the printing and exit code of each target.
Not here: the checks themselves (`validation/`).
Imports: `validation` and below.
"""

import argparse

from .captures import run_captures
from .files import (
    run_actions,
    run_can,
    run_can_buses,
    run_groups,
    run_signals,
    run_states,
    run_variants,
)
from .pids import run_pids

NAME = "validate"
ALIASES = ["val"]

__all__ = ["NAME", "add_parser", "run"]


def add_parser(subparsers):
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Check a profile's ecus/, profile.yaml, and captures/ against their schemas",
        description="Validate a profile's data files against their schemas and\n"
        "report problems.\n\n"
        "Pick a target (default: all):\n"
        "  pids      the per-ECU ecus/ files (identity/scan_log/dtcs/pids/...)\n"
        "  captures  the captures/ payload files (+ soft warnings, see below)\n"
        "  ecus      alias for pids\n"
        "  states    vehicle_states.yaml (vehicle power-state vocabulary + predicates)\n"
        "  can-buses can_buses.yaml (per-profile CAN bus segment vocabulary)\n"
        "  signals   signals/ broadcast signal-definition files (domain B)\n"
        "  groups    groups.yaml (named capture/monitor selector groups)\n"
        "  actions   actions.yaml (guarded profile actions)\n"
        "  variants  variants.yaml (the car's variants and their metadata)\n"
        "  can       captures/can/index.yaml (raw-CAN log index)\n"
        "  all       everything above\n\n"
        "`validate captures` also emits soft warnings for out-of-vocabulary vehicle\n"
        "states, SID/PID/DID echo mismatches (misfiled frames), replies that look\n"
        "like another ECU's answer to the same PID (by length or identical bytes),\n"
        "multi-DID replies stored whole under their first DID (repair with\n"
        "`captures split-batched`), non-hex payloads (e.g. a stored 'NO DATA'), and\n"
        "untimed payload captures.\n"
        "Pass --strict to promote the untimed-payload warning to an error — the CI /\n"
        "new-data gate.\n\n"
        "`validate states` resolves every `when:` predicate's ECU.PARAM references\n"
        "against the ecus/ registry: a predicate naming a signal that does not exist\n"
        "can never match, and the evaluator cannot report it (a missing signal is\n"
        "indistinguishable from a not-polled one), so this is the only place a\n"
        "renamed or typo'd signal name in a predicate is caught.\n\n"
        "Run this after editing ecus/ or adding captures; `canair pids` already\n"
        "validates each edit, so this is the whole-profile check.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair validate                     # validate everything (pids + captures + states)
  canair validate --stats             # + a count summary (ECUs/PIDs/params/verified)
  canair validate pids                # just the ecus/ definition files
  canair validate captures            # just captures/ (with soft warnings)
  canair validate captures --strict   # treat untimed-payload warnings as errors (CI)
  canair validate states              # just vehicle_states.yaml
  canair validate pids ecus/bms.yaml  # a specific ECU file only
""",
    )
    parser.add_argument(
        "target",
        nargs="?",
        choices=[
            "pids",
            "captures",
            "ecus",
            "states",
            "can-buses",
            "signals",
            "groups",
            "actions",
            "variants",
            "can",
            "all",
        ],
        default="all",
        help="What to validate (default: all)",
    )
    parser.add_argument(
        "files", nargs="*", help="Specific ecus/ files (only with target=pids/ecus)"
    )
    parser.add_argument("--stats", action="store_true", help="Show parameter statistics (pids)")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat soft warnings that gate new data (currently: untimed payload "
        "captures) as errors — for CI / new-capture checks",
    )
    parser.add_argument(
        "--max-untimed",
        type=int,
        default=None,
        metavar="N",
        dest="max_untimed",
        help="Fail (exit 1) if the profile has MORE than N untimed payload captures "
        "— a ratchet baseline for CI. Writes are enforced to carry a timestamp, so "
        "the count only ever falls; lower N as it drops. Distinct from --strict "
        "(which forbids any untimed row)",
    )
    parser.add_argument(
        "--max-untagged",
        type=int,
        default=None,
        metavar="N",
        dest="max_untagged",
        help="Fail (exit 1) if a profile with several variants has MORE than N capture "
        "sessions that name no car — a ratchet baseline for CI, like --max-untimed",
    )
    parser.add_argument(
        "--show-untimed",
        action="store_true",
        dest="show_untimed",
        help="List untimed payload captures per file instead of collapsing them to a "
        "single count — they otherwise drown the echo/quality warnings that need "
        "attention (the count is always reported in the footer)",
    )
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    strict = getattr(args, "strict", False)
    max_untimed = getattr(args, "max_untimed", None)
    show_untimed = getattr(args, "show_untimed", False)
    if args.target in ("pids", "ecus"):
        return run_pids(args.files or None, args.stats)
    if args.target == "captures":
        return run_captures(
            strict=strict,
            max_untimed=max_untimed,
            show_untimed=show_untimed,
            max_untagged=getattr(args, "max_untagged", None),
        )
    if args.target == "states":
        return run_states()
    if args.target == "can-buses":
        return run_can_buses()
    if args.target == "signals":
        return run_signals()
    if args.target == "groups":
        return run_groups()
    if args.target == "actions":
        return run_actions()
    if args.target == "variants":
        return run_variants()
    if args.target == "can":
        return run_can()
    # all: the ecus/ files are validated once via run_pids (they are the registry).
    rc_p = run_pids(None, args.stats)
    print()
    rc_c = run_captures(
        strict=strict,
        max_untimed=max_untimed,
        show_untimed=show_untimed,
        max_untagged=getattr(args, "max_untagged", None),
    )
    print()
    rc_s = run_states()
    print()
    rc_cb = run_can_buses()
    print()
    rc_sig = run_signals()
    print()
    rc_grp = run_groups()
    print()
    rc_act = run_actions()
    print()
    rc_var = run_variants()
    print()
    rc_can = run_can()
    return rc_p or rc_c or rc_s or rc_cb or rc_sig or rc_grp or rc_act or rc_var or rc_can
