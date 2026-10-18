#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""``canair`` — unified CAN/UDS/KWP2000 diagnostic reverse-engineering CLI.

A single entry point dispatching to subcommands (query, scan, decode,
captures, coverage, research, pids, validate, wican, bix, ...). Run
``canair <command> --help`` for command-specific help.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import NoReturn

from canlib.commands import iter_command_modules

# Global options (before the subcommand) that consume a following value. Used by
# _inject_default_subcommand to find the command token.
_GLOBAL_OPTS_WITH_VALUE = {"--profile", "--profiles-dir"}
# Command groups that default to a kind when the token after them isn't a known
# sub-kind. Maps command -> (known kinds, default kind).
#
# The kind sets are duplicated from each command's subparsers because injection
# runs on raw argv, before a parser exists. A kind missing here is not an error —
# it is silently rewritten into the default kind and then rejected as a stray
# argument, which is why tests/test_cli_group_defaults.py pins each set against
# the real subparsers.
_GROUP_DEFAULTS = {
    "scan": ({"range", "iocontrol", "routines", "sessions"}, "range"),
    "ecu": ({"show", "add", "rename"}, "show"),
    # A bare token after `states` (e.g. `canair states READY`) is a state name to
    # look up (which ECUs are readable in it), routed through the `list` kind.
    "states": (
        {
            "list",
            "add",
            "rm",
            "rename",
            "set-description",
            "set-predicate",
            "set-implies",
            "set-excludes",
        },
        "list",
    ),
    # The uds/can domain-kind spine (ingest/list/analyze). A bare invocation
    # defaults to the mature domain-A (uds) surface, preserving muscle memory.
    "captures": ({"uds", "can", "migrate", "migrate-rx", "merge-driver"}, "uds"),
    "correlate": ({"uds", "can"}, "uds"),
    "hunt": ({"uds", "can"}, "uds"),
    "investigate": ({"uds", "can"}, "uds"),
}

# Alias -> canonical command name for the group-default injection below, so a
# command alias (e.g. `cap` for `captures`) still gets its default kind injected.
# argparse resolves aliases itself, but _inject_default_subcommand runs on raw
# argv before argparse, so it must know the alias maps to a _GROUP_DEFAULTS key.
_GROUP_ALIASES = {"cap": "captures"}

# Intuitive-but-wrong top-level tokens that are really subcommands living under
# another command. When argparse rejects one as an invalid <command> choice, we
# append a pointer to where it actually lives, so a user typing `canair mode …`
# is steered to `canair wican mode` instead of only being shown the raw choice
# list. Keyed conservatively: every value must be a real command path.
_RELOCATED_COMMANDS = {
    "mode": "wican mode",
    "autopid": "wican autopid",
    "auto-pid": "wican autopid",
    "auto_pid": "wican autopid",
}

_INVALID_CHOICE_RE = re.compile(r"invalid choice: '([^']+)'")


def _relocation_hint(message: str) -> str | None:
    """A pointer to a relocated command, if ``message`` rejected one as <command>."""
    m = _INVALID_CHOICE_RE.search(message)
    if m is None:
        return None
    dest = _RELOCATED_COMMANDS.get(m.group(1))
    if dest is None:
        return None
    return f"hint: '{m.group(1)}' is not a canair command — try 'canair {dest}'."


class _CanairParser(argparse.ArgumentParser):
    """Top-level parser that points a relocated command to its real home."""

    def error(self, message: str) -> NoReturn:
        hint = _relocation_hint(message)
        if hint is None:
            super().error(message)
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n\n{hint}\n")


# Commands that manage/report versions themselves or shouldn't be interrupted by
# an update notice (they have their own output contract or run non-interactively).
_UPDATE_CHECK_SKIP_COMMANDS = {"update", "completion", "config"}


def _update_check_allowed(args) -> bool:
    """Whether to run the background update check / print a notice this run.

    Conservative, mirroring the first-run gate: interactive TTY only, a command
    that actually dispatches (not bare ``canair`` / ``--help`` / ``--version``),
    and not one of the self-managing commands.
    """
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        return False
    if getattr(args, "func", None) is None:
        return False
    if getattr(args, "command", None) in _UPDATE_CHECK_SKIP_COMMANDS:
        return False
    return True


def _rewrite_help_tokens(argv: list[str]) -> list[str]:
    """Accept a bare ``help`` word as a universal alias for ``-h``/``--help``.

    ``canair help``            -> ``canair -h``            (top-level help)
    ``canair help decode``     -> ``canair decode -h``     (command help)
    ``canair decode help``     -> ``canair decode -h``     (command help)
    ``canair captures uds help`` -> ``canair captures uds -h`` (kind help)

    Only a leading ``help`` (after any global options) or a trailing ``help``
    token is treated specially, so an argument that merely *contains* "help"
    (an ECU/PID/label value) is never clobbered. If ``-h``/``--help`` is already
    present, argv is returned unchanged.
    """
    if not argv or any(tok in ("-h", "--help") for tok in argv):
        return argv

    # Skip leading global options to find the first meaningful token.
    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok in _GLOBAL_OPTS_WITH_VALUE:
            i += 2
            continue
        if tok.startswith("--") and "=" in tok:  # --profile=NAME
            i += 1
            continue
        break

    # Leading `help [rest...]` -> `[rest...] -h` (rest may be empty = top-level).
    if i < n and argv[i] == "help":
        return [*argv[:i], *argv[i + 1 :], "-h"]

    # Trailing `... help` -> `... -h`.
    if argv[-1] == "help":
        return [*argv[:-1], "-h"]

    return argv


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
    group = _GROUP_DEFAULTS.get(_GROUP_ALIASES.get(argv[i], argv[i]))
    if group is None:
        return argv
    kinds, default_kind = group
    j = i + 1
    # A kind or a help flag already present → leave as-is.
    if j < n and (argv[j] in kinds or argv[j] in ("-h", "--help")):
        return argv
    # Otherwise inject the default kind right after the command.
    return [*argv[:j], default_kind, *argv[j:]]


class _VersionAction(argparse.Action):
    """``--version``, resolved only when actually asked for.

    The reported version is provenance-bearing — from a git checkout it names the
    branch and commit, which costs a ``git`` call (see :mod:`canlib.build_info`).
    argparse's stock ``version`` action captures its string at ``add_argument``
    time, i.e. while *building* the parser, which would tax every single canair
    invocation; this one defers the lookup to the flag's use.
    """

    def __init__(
        self,
        option_strings,
        dest=argparse.SUPPRESS,
        default=argparse.SUPPRESS,
        help=None,
    ):
        super().__init__(
            option_strings=option_strings, dest=dest, default=default, nargs=0, help=help
        )

    def __call__(self, parser, namespace, values, option_string=None):
        from canlib.build_info import full_version

        print(f"{parser.prog} {full_version()}")
        parser.exit()


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands registered."""
    from canlib.commands._categories import CategorizedHelpFormatter

    parser = _CanairParser(
        prog="canair",
        description=__doc__,
        formatter_class=CategorizedHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action=_VersionAction,
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

    from canlib.commands._domain import apply_domain_tags

    apply_domain_tags(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()

    if argv is None:
        argv = sys.argv[1:]
    argv = _rewrite_help_tokens(argv)
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

    # One-time, best-effort migration of a legacy wican_addresses block to the
    # richer devices: form (never blocks; runtime precedence handles the rest).
    from canlib.devices_migrate import maybe_auto_migrate

    maybe_auto_migrate()

    # On a genuine first run, offer to pick or create a vehicle profile (only
    # when interactive and the command actually needs one).
    from canlib.first_run import run_first_run_setup, should_offer

    if should_offer(args, seeded=seeded):
        run_first_run_setup(args)

    from canlib.capture_io import LegacyCaptureError
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

    # Fire a non-blocking, cached update check (interactive only; opt-out via
    # config/env). Never blocks or affects the command's outcome.
    from canlib import update_check

    check_updates = _update_check_allowed(args)
    if check_updates:
        update_check.maybe_check_in_background()

    try:
        result = func(args)
    except (ProfileError, LegacyCaptureError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if check_updates:
        update_check.print_notice_if_any()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
