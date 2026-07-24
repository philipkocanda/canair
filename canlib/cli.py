#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""``canair`` — unified CAN/UDS/KWP2000 diagnostic reverse-engineering CLI.

A single entry point dispatching to subcommands (query, scan, decode,
captures, coverage, research, pids, validate, wican, bix, ...). Run
``canair <command> --help`` for command-specific help.
"""

from __future__ import annotations

import argparse
import sys

from canlib import __version__
from canlib.commands import iter_command_modules

# Global options (before the subcommand) that consume a following value. Used by
# _inject_default_subcommand to find the command token.
_GLOBAL_OPTS_WITH_VALUE = {"--profile", "--profiles-dir"}
# Command groups that default to a kind when the token after them isn't a known
# sub-kind. Maps command -> (known kinds, default kind).
_GROUP_DEFAULTS = {
    "scan": ({"range", "iocontrol", "routines", "sessions"}, "range"),
    "ecu": ({"show", "add"}, "show"),
}


def _inject_default_subcommand(argv: list[str]) -> list[str]:
    """Make command groups default to a kind when none is given.

    `canair scan BMS`  -> `canair scan range BMS`   (bare = range wizard/sweep)
    `canair ecu BMS`   -> `canair ecu show BMS`      (bare = list/detail)
    `canair scan -h`   -> unchanged (show the group help)
    `canair ecu add …` -> unchanged (explicit kind).

    This keeps the pre-group muscle memory (`canair scan/ecu <ECU>`) working now
    that those are command groups.
    """
    i = 0
    n = len(argv)
    # Skip leading global options to find the command token.
    while i < n:
        tok = argv[i]
        if tok in _GLOBAL_OPTS_WITH_VALUE:
            i += 2
            continue
        if tok.startswith("--") and "=" in tok:  # --profile=NAME
            i += 1
            continue
        break
    if i >= n:
        return argv
    group = _GROUP_DEFAULTS.get(argv[i])
    if group is None:
        return argv
    kinds, default_kind = group
    j = i + 1
    # A kind or a help flag already present → leave as-is.
    if j < n and (argv[j] in kinds or argv[j] in ("-h", "--help")):
        return argv
    # Otherwise inject the default kind right after the command.
    return [*argv[:j], default_kind, *argv[j:]]


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands registered."""
    parser = argparse.ArgumentParser(
        prog="canair",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show the canair version and exit.",
    )
    parser.add_argument(
        "--profile",
        metavar="NAME|PATH",
        default=None,
        help="Vehicle profile to use (name or path). Overrides CANAIR_PROFILE / config.",
    )
    parser.add_argument(
        "--profiles-dir",
        metavar="DIR",
        default=None,
        help="Extra directory to search for vehicle profiles.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    for module in iter_command_modules():
        module.add_parser(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()

    if argv is None:
        argv = sys.argv[1:]
    argv = _inject_default_subcommand(argv)

    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args(argv)

    # Ensure ~/.config/canair (and profiles/) exists so no manual setup is needed.
    from canlib.config import ensure_config_dir

    seeded = ensure_config_dir()

    # On a genuine first run, offer to pick or create a vehicle profile (only
    # when interactive and the command actually needs one).
    from canlib.first_run import run_first_run_setup, should_offer

    if should_offer(args, seeded=seeded):
        run_first_run_setup(args)

    from canlib.profile import ProfileError, set_active

    # Resolve the active vehicle profile before dispatching.
    if (
        getattr(args, "profile", None) is not None
        or getattr(args, "profiles_dir", None) is not None
    ):
        try:
            set_active(args.profile, args.profiles_dir)
        except ProfileError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 1
    try:
        result = func(args)
    except ProfileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
