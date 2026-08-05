"""Which mode did the user ask for — and is the flag combination legal?

``captures uds`` exposes its modes as *flags* rather than sub-subcommands, so
argparse can express only part of the exclusion rules (the ``standalone`` mutually
exclusive group). The rest — "``--delete`` needs a QUERY", "``--set-state`` needs
a scope filter", "an aggregate mode takes no QUERY" — were ~100 lines of
hand-rolled ``if``/``print``/``return 2`` inline in ``run``, reachable only by
invoking the whole CLI.

This module holds that policy as data plus one pure function, so the rules are
stated once and testable directly (see ``tests/test_captures_mode_select.py``).
:func:`resolve_mode` decides; :class:`ModeError` carries how to report a rejection
so the caller stays a three-liner. The user-facing surface is unchanged — same
flags, same messages, same exit codes.

The flag-as-mode design is itself the underlying wart (proper sub-subcommands
would let argparse own all of this), but changing it is a breaking CLI change —
see ``plans/2026-08-05-captures-command-package-split.md``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Literal

Mode = Literal[
    "recover",
    "summary",
    "sessions",
    "backfill_states",
    "set_state",
    "delete",
    "latest",
    "diff",
    "step",
    "list",
]

# Aggregate/whole-store modes that take no QUERY, in dispatch precedence order.
STANDALONE_MODES: tuple[Mode, ...] = ("summary", "sessions", "backfill_states", "set_state")

# How those modes are named back to the user in a rejection message.
_STANDALONE_LABEL = "--summary/--sessions/--backfill-states/--set-state"

# View modifiers on top of a QUERY, in dispatch precedence order.
VIEW_MODES: tuple[Mode, ...] = ("diff", "step")

# Scope filters that narrow which sessions a mutating mode may touch.
_SCOPE_ATTRS = ("label", "state", "date", "today", "since", "until", "last_sessions")


@dataclass(frozen=True)
class ModeError:
    """A rejected flag combination, with everything needed to report it."""

    message: str
    to_stderr: bool = True
    with_ecu_hint: bool = False
    code: int = 2

    def report(self) -> int:
        """Print the rejection the way the command always has; return its exit code."""
        print(self.message, file=sys.stderr if self.to_stderr else sys.stdout)
        if self.with_ecu_hint:
            from canlib.commands._hints import ecu_hint

            print(ecu_hint())
        return self.code


def _selected(args, mode: Mode) -> bool:
    """Whether ``mode``'s flag was given (``--set-state`` is value-carrying)."""
    if mode == "set_state":
        return args.set_state is not None
    return bool(getattr(args, mode))


def has_scope_filter(args) -> bool:
    """Whether any scope filter narrows the selection (``--label``/``--date``/…)."""
    return any(getattr(args, attr, None) for attr in _SCOPE_ATTRS)


def resolve_mode(args, query: str) -> Mode | ModeError:
    """The mode to run, or the reason the flag combination is refused.

    Checks are ordered as they historically fired, so the message a given bad
    invocation produces is unchanged. Several combinations are already impossible
    (argparse's ``standalone`` group excludes them); they are still checked here
    because ``run`` is also reached directly from tests, and a guard that can only
    be proven unreachable by reading the parser is not worth removing.
    """
    if args.recover:
        return "recover"

    if args.delete and not query:
        return ModeError(
            "error: --delete requires a QUERY selecting what to delete "
            "(e.g. `canair captures uds OBC 2101 --delete`). Refusing to delete "
            "everything. Narrow with the QUERY and/or scope flags (--since/--state/…)."
        )

    # --set-state writes the same state to every scope-selected session, so it
    # must be narrowed by a scope filter — refuse a bare invocation that would
    # relabel the entire capture history.
    if _selected(args, "set_state") and not has_scope_filter(args):
        return ModeError(
            "error: --set-state requires a scope filter selecting which sessions "
            "to tag (e.g. --label 'ACC only', --date 2026-04-15). Refusing to set "
            "state on every session."
        )

    if args.limit < 0:
        return ModeError("error: --limit must be >= 0 (0 = no cap)")

    # The aggregate modes take no QUERY. --latest is a dedup-per-PID *view* that
    # reads its ECU/PID selection from the QUERY (like the default list view), so
    # it belongs with the QUERY modes below, not here.
    standalone = next((m for m in STANDALONE_MODES if _selected(args, m)), None)
    if standalone is not None:
        if query:
            return ModeError(f"error: {_STANDALONE_LABEL} do not take a QUERY argument")
        if args.latest:
            return ModeError(f"error: --latest cannot be combined with {_STANDALONE_LABEL}")
        if args.diff or args.step:
            return ModeError(f"error: --diff/--step cannot be combined with {_STANDALONE_LABEL}")
        return standalone

    if args.latest:
        if args.diff or args.step:
            return ModeError("error: --latest cannot be combined with --diff/--step")
        return "latest"

    if args.delete:
        return "delete"

    if not query:
        return ModeError(
            "Specify a QUERY to look up captures, e.g. `canair captures BMS 2102` "
            "(or use --summary / --sessions / --latest).\n",
            to_stderr=False,
            with_ecu_hint=True,
        )

    return next((m for m in VIEW_MODES if _selected(args, m)), "list")
