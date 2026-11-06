"""The ``canair contribute`` pipeline: gates first, then the actions they guard."""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

from canair.cli.profile_context import active
from canair.profile import ProfileError, alias_index
from canair.render.console import console as make_console
from canair.render.console import escape

from . import gates
from .report import Reporter, Stop
from .stage import discard_staged, report_diff, stage_contribution
from .state import Contribution
from .submit import submit


def run(args) -> int:
    rep = Reporter(console=make_console(), json_mode=bool(args.json), yes=bool(args.yes))
    try:
        return contribute(args, rep)
    except Stop as stop:
        return stop.code


def contribute(args, rep: Reporter) -> int:
    """The contribution pipeline: pre-flight gates, then the actions they guard.

    Each step either advances the run or ends it by raising ``Stop`` — which is
    what keeps the flow readable as the sequence a contributor experiences.
    """
    profile, branch = _resolve_profile(args, rep)
    include_captures = bool(args.captures)

    gates.snapshot(rep, profile)
    gates.validate(rep, profile)
    pre = gates.environment(rep, profile, branch)
    gates.capture_size(rep, profile, include_captures=include_captures)
    workspace = gates.workspace(rep, args, profile, pre)

    run_ = Contribution(
        args=args,
        rep=rep,
        profile=profile,
        branch=branch,
        include_captures=include_captures,
        pre=pre,
        workspace=workspace,
    )
    try:
        if (no_changes := stage_contribution(run_)) is not None:
            return no_changes
        if args.diff:
            return report_diff(run_)

        run_.findings = gates.privacy(
            rep,
            profile,
            pre,
            workspace,
            include_captures=include_captures,
            rollback=run_.rollback,
        )
        gates.captures(
            rep,
            profile,
            pre,
            workspace,
            include_captures=include_captures,
            replaced=run_.replaced,
        )
        return submit(run_)
    finally:
        discard_staged(run_)


def _resolve_profile(args, rep: Reporter) -> tuple[Any, str]:
    """Resolve the profile being contributed and the branch to push it on."""
    try:
        profile = active()
    except ProfileError as e:
        error = str(e)  # `e` is unbound outside the except block
        rep.refuse(error, human=lambda: rep.console.print(f"[red]error:[/red] {escape(error)}"))

    profile = _upstream_named(profile, rep)
    branch = args.branch or f"contribute/{profile.name}-{date.today():%Y%m%d}"
    rep.note(
        profile=profile.name,
        source=str(profile.root),
        branch=branch,
        include_captures=bool(args.captures),
    )
    return profile, branch


def _upstream_named(profile, rep: Reporter):
    """The profile as upstream names it: an old profile name maps to its successor.

    A copy adopted before a rename still carries the old name, and contributing it
    as-is would recreate ``profiles/<old name>/`` beside the renamed profile. Only
    the destination changes; the source is still read from the copy's own root.
    """
    target = alias_index().target(profile.name)
    if target is None:
        return profile
    rep.note(renamed_from=profile.name)
    if not rep.json_mode:
        rep.console.print(
            f"  [yellow]note:[/yellow] '{escape(profile.name)}' was renamed upstream; contributing "
            f"it as [cyan]profiles/{escape(target)}/[/cyan]."
        )
    return dataclasses.replace(profile, name=target)
