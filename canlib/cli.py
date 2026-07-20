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

from canlib.commands import iter_command_modules


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands registered."""
    parser = argparse.ArgumentParser(
        prog="canair",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
    subparsers.required = True

    for module in iter_command_modules():
        module.add_parser(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()

    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args(argv)

    from canlib.profile import ProfileError, set_active

    # Resolve the active vehicle profile before dispatching.
    if getattr(args, "profile", None) is not None or getattr(args, "profiles_dir", None) is not None:
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
