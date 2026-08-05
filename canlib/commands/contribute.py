"""``canair contribute`` — open a pull request for the active profile.

A one-command path to share a reverse-engineered profile (definitions and
captures) upstream, without the manual fork/clone/branch/push dance. It works
regardless of where the profile is stored (bundled repo, ``~/.config`` user dir,
or a ``--path`` bundle): the profile is copied into a managed fork checkout and a
PR is opened via the GitHub CLI (``gh``).

The flow is a **linear pipeline**: pre-flight gates first, then the actions they
guard (stage → review → submit). Any step can end the run by raising
:class:`~canlib.commands._contribute_report.Stop`, which is what keeps
:func:`_contribute` readable as the sequence a contributor experiences. This
module owns the CLI surface and the actions; its collaborators own the rest:

* :mod:`canlib.commands._contribute_gates` — the pre-flight policy (is the source
  fit? is the environment ready? has the user consented?).
* :mod:`canlib.commands._contribute_report` — the human/``--json`` duality every
  outcome is reported through.
* :mod:`canlib.contribute` — the git/gh orchestration (device-free testable).
* :mod:`canlib.pii` — the privacy scan the PII gate runs.

The user-facing ``--help`` text is :data:`_DESCRIPTION`, deliberately separate
from this docstring so internal structure never leaks into it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .. import contribute as C
from ..profile import ProfileError, active
from . import _contribute_gates as gates
from ._contribute_report import Reporter, Stop, confirm, findings_json, rollback_json

NAME = "contribute"

_DESCRIPTION = """\
Open a pull request contributing the active profile upstream.

A one-command path to share a reverse-engineered profile (its definitions, and by
default its captures) without the manual fork/clone/branch/push dance — it works
wherever the profile is stored. Before anything leaves your machine it validates
the profile, scans it for anything that could identify or locate you, warns if the
source looks stale, and asks you to confirm.
"""

# Warn before contributing a captures/ dir larger than this (bytes).
_CAPTURES_WARN_BYTES = 25 * 1024 * 1024


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=["share"],
        help="Open a pull request contributing the active profile upstream",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair contribute                     # PR the active profile (definitions + captures)
  canair --profile ev6 contribute       # PR a specific profile
  canair contribute --no-captures       # contribute definitions only
  canair contribute --diff              # show what would be contributed, then stop
  canair contribute --dry-run           # prepare the branch + commit locally; no push/PR
  canair contribute --yes --json        # non-interactive (agents/CI); emit the PR URL

requires the GitHub CLI:
  install:  brew install gh   (macOS)  ·  winget install GitHub.cli   (Windows)
            https://github.com/cli/cli#installation
  sign in:  gh auth login
""",
    )
    caps = parser.add_mutually_exclusive_group()
    caps.add_argument(
        "--captures",
        dest="captures",
        action="store_true",
        default=True,
        help="Include the captures/ evidence (default)",
    )
    caps.add_argument(
        "--no-captures",
        dest="captures",
        action="store_false",
        help="Contribute definitions only (ecus/, profile.yaml, states, buses, groups, signals)",
    )
    parser.add_argument(
        "--branch", help="Branch name to push (default: contribute/<profile>-<date>)"
    )
    parser.add_argument("--title", help="Pull request title (default: generated)")
    parser.add_argument("--body", help="Pull request body (default: generated checklist)")
    parser.add_argument(
        "--repo-dir",
        help="Use this existing canair checkout (with a fork as 'origin') instead of "
        "the managed fork clone",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare the branch and commit locally, but do not push or open a PR",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show the diff this contribution would submit, then stop (no commit/push/PR)",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable summary")
    parser.set_defaults(func=run)
    return parser


@dataclass
class _Contribution:
    """What the action stages operate on, once the gates have cleared the run.

    Constructed only after the environment and workspace gates have resolved
    ``pre``/``workspace``, so no stage has to defend against a half-built run.
    """

    args: argparse.Namespace
    rep: Reporter
    profile: Any
    branch: str
    include_captures: bool
    pre: C.Preflight
    workspace: Path
    mode: str = ""
    rollback: list[tuple[str, int]] = field(default_factory=list)
    findings: list[Any] = field(default_factory=list)

    @property
    def target(self) -> str:
        """Where the branch is pushed, for prose."""
        return C.UPSTREAM_REPO if self.mode == C.MODE_DIRECT else "your fork"


def run(args) -> int:
    from rich.console import Console

    rep = Reporter(console=Console(), json_mode=bool(args.json), yes=bool(args.yes))
    try:
        return _contribute(args, rep)
    except Stop as stop:
        return stop.code


def _contribute(args, rep: Reporter) -> int:
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
    workspace = gates.workspace(rep, args, profile)

    run_ = _Contribution(
        args=args,
        rep=rep,
        profile=profile,
        branch=branch,
        include_captures=include_captures,
        pre=pre,
        workspace=workspace,
    )
    if (no_changes := _stage_contribution(run_)) is not None:
        return no_changes
    if args.diff:
        return _report_diff(run_)

    run_.findings = gates.privacy(
        rep,
        profile,
        pre,
        workspace,
        include_captures=include_captures,
        rollback=run_.rollback,
    )
    return _submit(run_)


# --- stages -----------------------------------------------------------------


def _resolve_profile(args, rep: Reporter) -> tuple[Any, str]:
    """Resolve the profile being contributed and the branch to push it on."""
    try:
        profile = active()
    except ProfileError as e:
        error = str(e)  # `e` is unbound outside the except block
        rep.refuse(error, human=lambda: rep.console.print(f"[red]error:[/red] {error}"))

    branch = args.branch or f"contribute/{profile.name}-{date.today():%Y%m%d}"
    rep.note(
        profile=profile.name,
        source=str(profile.root),
        branch=branch,
        include_captures=bool(args.captures),
    )
    return profile, branch


def _stage_contribution(run_: _Contribution) -> int | None:
    """Sync the workspace, branch, and copy the profile in.

    Returns an exit code when the run is already complete (the profile matches
    upstream, so there is nothing to contribute), else ``None``.
    """
    rep, pre, workspace = run_.rep, run_.pre, run_.workspace

    if not rep.json_mode:
        rep.console.print(f"\n  reading profile from: [dim]{run_.profile.root}[/dim]")
        rep.console.print(f"  staging in workspace: [dim]{workspace}[/dim]")
        rep.console.print("  syncing (first run clones — may take a moment) …")

    steps, mode, ok = C.ensure_workspace(pre, workspace)
    run_.mode = mode
    rep.note(mode=mode)
    if not ok:
        failed = steps[-1] if steps else None
        detail = failed.output if failed else "unknown error"

        def explain() -> None:
            rep.console.print("  [red]failed to prepare the workspace:[/red]")
            rep.console.print(f"  [dim]{detail}[/dim]")
            rep.console.print("\n" + C.manual_instructions(run_.profile.name, run_.branch))

        rep.fail("workspace prep failed", detail=detail, human=explain)
    if not rep.json_mode:
        rep.console.print(f"  mode: [cyan]{mode}[/cyan] (will push to {run_.target})")

    br = C.start_branch(pre, workspace, run_.branch)
    if not br.ok:
        rep.fail(
            "branch failed",
            detail=br.output,
            human=lambda: rep.console.print(
                f"  [red]could not create branch {run_.branch}:[/red] [dim]{br.output}[/dim]"
            ),
        )

    C.copy_profile(run_.profile, workspace, include_captures=run_.include_captures, warn=rep.warn)

    if not C.has_changes(pre, workspace, run_.profile.name):
        msg = "No changes to contribute — the upstream profile already matches yours."
        return rep.done(
            lambda: rep.console.print(f"\n  [green]{msg}[/green]"),
            no_changes=True,
            message=msg,
        )

    # Curated definitions normally only grow, so a diff that *removes* committed
    # upstream lines suggests a stale source. Computed here because both --diff
    # and the privacy gate report it.
    run_.rollback = C.definition_rollback(pre, workspace, run_.profile.name)
    return None


def _report_diff(run_: _Contribution) -> int:
    """``--diff``: show exactly what would be contributed, then stop."""
    rep, pre, workspace = run_.rep, run_.pre, run_.workspace
    diff = C.diff_profile(pre, workspace, run_.profile.name)

    def show() -> None:
        from rich.syntax import Syntax

        rep.console.print(f"\n  diff of [cyan]profiles/{run_.profile.name}/[/cyan] vs upstream:\n")
        if diff.strip():
            rep.console.print(Syntax(diff, "diff", theme="ansi_dark", background_color="default"))
        else:
            rep.console.print("  [dim](no textual diff)[/dim]")
        if run_.rollback:
            rep.rollback_warning(run_.rollback)
        rep.console.print("\n  [dim](diff only — nothing committed, pushed, or opened)[/dim]")
        rep.console.print("  Re-run without [cyan]--diff[/cyan] to contribute it.")

    return rep.done(show, diff=diff, rollback=rollback_json(run_.rollback), pr_url=None)


def _submit(run_: _Contribution) -> int:
    """Commit the staged profile, then either stop (``--dry-run``) or open the PR."""
    rep = run_.rep
    _commit(run_)
    if run_.args.dry_run:

        def prepared() -> None:
            rep.console.print(
                f"\n  [green]✓ Prepared[/green] branch [cyan]{run_.branch}[/cyan] "
                f"in {run_.workspace}"
            )
            rep.console.print("  [dim](dry run — nothing pushed; no PR opened)[/dim]")

        return rep.done(prepared, dry_run=True, findings=findings_json(run_.findings), pr_url=None)
    return _push_and_open_pr(run_)


def _commit(run_: _Contribution) -> None:
    """Commit the copied-in profile onto the contribution branch."""
    rep, pre, workspace, args = run_.rep, run_.pre, run_.workspace, run_.args

    commit = C.commit_profile(
        pre,
        workspace,
        run_.profile.name,
        args.title or C.default_message(run_.profile, include_captures=run_.include_captures),
    )
    if not commit.ok:
        rep.fail(
            "commit failed",
            detail=commit.output,
            human=lambda: rep.console.print(
                f"  [red]commit failed:[/red] [dim]{commit.output}[/dim]"
            ),
        )


def _push_and_open_pr(run_: _Contribution) -> int:
    """The point of no return: confirm, push the branch, open the pull request."""
    rep, pre, workspace, args = run_.rep, run_.pre, run_.workspace, run_.args

    if not rep.json_mode:
        rep.console.print(
            f"\n  Ready to push [cyan]{run_.branch}[/cyan] to {run_.target} and open a pull "
            f"request against [cyan]{C.UPSTREAM_REPO}[/cyan]."
        )
        rep.console.print(
            "  [dim](tip: `canair contribute --diff` shows the full change first)[/dim]"
        )
    if not confirm("Push and open the PR?", rep.yes, json_mode=rep.json_mode):
        rep.refuse(
            "push not confirmed (pass --yes to proceed non-interactively)",
            human=lambda: rep.console.print(
                "  Aborted — nothing was pushed. Your branch is prepared locally in the workspace."
            ),
        )

    if not rep.json_mode:
        rep.console.print(f"  pushing [cyan]{run_.branch}[/cyan] to {run_.target} …")
    push = C.push_branch(pre, workspace, run_.branch)
    if not push.ok:
        rep.fail(
            "push failed",
            detail=push.output,
            human=lambda: rep.console.print(f"  [red]push failed:[/red] [dim]{push.output}[/dim]"),
        )

    pr = C.create_pr(
        pre,
        workspace,
        title=args.title
        or C.default_message(run_.profile, include_captures=run_.include_captures).splitlines()[0],
        body=args.body or C.default_pr_body(run_.profile, include_captures=run_.include_captures),
        head=C.pr_head(pre, run_.branch, run_.mode),
    )
    if not pr.ok:

        def explain() -> None:
            rep.console.print(f"  [red]opening the PR failed:[/red] [dim]{pr.output}[/dim]")
            rep.console.print("  Your branch was pushed — you can open the PR manually on GitHub.")

        rep.fail("pr create failed", detail=pr.output, human=explain)

    pr_url = pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else ""

    def celebrate() -> None:
        rep.console.print(f"\n  [green]✓ Pull request opened:[/green] {pr_url}")
        rep.console.print("  Thank you for contributing! 🎉")

    return rep.done(celebrate, dry_run=False, findings=findings_json(run_.findings), pr_url=pr_url)


# --- helpers ----------------------------------------------------------------
