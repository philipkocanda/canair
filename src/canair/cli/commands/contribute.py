"""``canair contribute`` — open a pull request for the active profile.

A one-command path to share a reverse-engineered profile (definitions and
captures) upstream, without the manual fork/clone/branch/push dance. It works
regardless of where the profile is stored (bundled repo, ``~/.config`` user dir,
or a ``--path`` bundle): the profile is copied into a managed fork checkout and a
PR is opened via the GitHub CLI (``gh``).

The flow is a **linear pipeline**: pre-flight gates first, then the actions they
guard (stage → review → submit). Any step can end the run by raising
:class:`~canair.cli.commands._contribute_report.Stop`, which is what keeps
:func:`_contribute` readable as the sequence a contributor experiences. This
module owns the CLI surface and the actions; its collaborators own the rest:

* :mod:`canair.cli.commands._contribute_gates` — the pre-flight policy (is the source
  fit? is the environment ready? has the user consented?).
* :mod:`canair.cli.commands._contribute_report` — the human/``--json`` duality every
  outcome is reported through.
* :mod:`canair.contribute` — the git/gh orchestration (device-free testable).
* :mod:`canair.contribute.source` — which of the source checkout's changes the
  contribution holds, so they can be dropped once the PR is open.
* :mod:`canair.contribute.pii` — the privacy scan the PII gate runs.

The user-facing ``--help`` text is :data:`_DESCRIPTION`, deliberately separate
from this docstring so internal structure never leaks into it.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ... import contribute as C
from ...core.prompt import confirm
from ...profile import ProfileError, active, alias_index
from ...render import glyphs
from ...render.console import console as make_console
from ...render.console import escape
from . import _contribute_gates as gates
from ._contribute_report import (
    Reporter,
    Stop,
    findings_json,
    replaced_json,
    rollback_json,
)

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
  canair contribute --no-logs           # leave the scrubbed diagnostics log out of the PR
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
    parser.add_argument(
        "--body",
        help="Pull request body (default: generated summary of the car, ECUs, states, "
        "sessions and link quality, plus a checklist)",
    )
    logs = parser.add_mutually_exclusive_group()
    logs.add_argument(
        "--include-logs",
        dest="include_logs",
        action="store_true",
        default=True,
        help="Attach the diagnostics log (canair logs) entries written while the contributed "
        "sessions were recording to the PR body, for debugging; device addresses, local "
        "paths, emails and VINs are scrubbed (default)",
    )
    logs.add_argument(
        "--no-logs",
        dest="include_logs",
        action="store_false",
        help="Leave the diagnostics log out of the PR body",
    )
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
    local = parser.add_mutually_exclusive_group()
    local.add_argument(
        "--discard-local",
        dest="local_changes",
        action="store_const",
        const="discard",
        help="Once the PR is open, drop the changes it carries from your canair checkout "
        "(backed up first), so pulling the merged PR does not conflict with them. Without "
        "this or --keep-local you are asked; with --yes or --json they are kept",
    )
    local.add_argument(
        "--keep-local",
        dest="local_changes",
        action="store_const",
        const="keep",
        help="Leave your checkout's uncommitted profile changes alone without asking",
    )
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
    staged: bool = False  # the profile is (being) copied into the workspace
    rollback: list[tuple[str, int]] = field(default_factory=list)
    replaced: list[C.CaptureReplacement] = field(default_factory=list)
    findings: list[Any] = field(default_factory=list)
    summary: Any = None  # canair.contribute.summary.ContributionSummary

    @property
    def target(self) -> str:
        """Where the branch is pushed, for prose."""
        return C.UPSTREAM_REPO if self.mode == C.MODE_DIRECT else "your fork"


def run(args) -> int:
    rep = Reporter(console=make_console(), json_mode=bool(args.json), yes=bool(args.yes))
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
    workspace = gates.workspace(rep, args, profile, pre)

    run_ = _Contribution(
        args=args,
        rep=rep,
        profile=profile,
        branch=branch,
        include_captures=include_captures,
        pre=pre,
        workspace=workspace,
    )
    try:
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
        gates.captures(
            rep,
            profile,
            pre,
            workspace,
            include_captures=include_captures,
            replaced=run_.replaced,
        )
        return _submit(run_)
    finally:
        _discard_staged(run_)


# --- stages -----------------------------------------------------------------


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


def _stage_contribution(run_: _Contribution) -> int | None:
    """Sync the workspace, branch, and copy the profile in.

    Returns an exit code when the run is already complete (the profile matches
    upstream, so there is nothing to contribute), else ``None``.
    """
    rep, pre, workspace = run_.rep, run_.pre, run_.workspace

    if not rep.json_mode:
        rep.console.print(f"\n  reading profile from: [dim]{escape(str(run_.profile.root))}[/dim]")
        rep.console.print(f"  staging in workspace: [dim]{escape(str(workspace))}[/dim]")
        rep.console.print("  syncing (first run clones — may take a moment) …")

    steps, mode, ok = C.ensure_workspace(pre, workspace)
    run_.mode = mode
    rep.note(mode=mode)
    if not ok:
        failed = steps[-1] if steps else None
        detail = failed.output if failed else "unknown error"

        def explain() -> None:
            rep.console.print("  [red]failed to prepare the workspace:[/red]")
            rep.console.print(f"  [dim]{escape(detail)}[/dim]")
            rep.console.print("\n" + escape(C.manual_instructions(run_.profile.name, run_.branch)))

        rep.fail("workspace prep failed", detail=detail, human=explain)
    if not rep.json_mode:
        rep.console.print(
            f"  mode: [cyan]{escape(mode)}[/cyan] (will push to {escape(run_.target)})"
        )

    _clear_leftovers(run_)

    br = C.start_branch(pre, workspace, run_.branch)
    if not br.ok:
        rep.fail(
            "branch failed",
            detail=br.output,
            human=lambda: rep.console.print(
                f"  [red]could not create branch {escape(run_.branch)}:[/red] "
                f"[dim]{escape(br.output)}[/dim]"
            ),
        )
    run_.staged = True

    _make_hermetic(run_)

    C.copy_profile(
        run_.profile,
        workspace,
        include_captures=run_.include_captures,
        warn=rep.warn,
        replaced=run_.replaced.append,
    )

    if not C.has_changes(pre, workspace, run_.profile.name):
        msg = "No changes to contribute — the upstream profile already matches yours."
        if not rep.json_mode:
            rep.console.print(f"\n  [green]{glyphs.OK} {msg}[/green]")
        local = None if run_.args.diff or run_.args.dry_run else _tidy_source(run_)
        return rep.done(lambda: None, no_changes=True, message=msg, local_changes=local)

    # Curated definitions normally only grow, so a diff that *removes* committed
    # upstream lines suggests a stale source. Computed here because both --diff
    # and the privacy gate report it.
    run_.rollback = C.definition_rollback(pre, workspace, run_.profile.name)
    run_.summary = _summarize(run_)
    return None


def _summarize(run_: _Contribution):
    """What the staged contribution adds, for the PR body and ``--json``."""
    from canair.contribute import summary as S

    try:
        from canair.ecus import build_rx_index

        rx_names = build_rx_index()
    except Exception:
        rx_names = {}
    name = run_.profile.name
    summary = S.summarize(
        run_.workspace / "profiles" / name / "captures",
        profile_name=name,
        base_reader=C.base_reader(run_.pre, run_.workspace),
        definition_files=C.changed_definition_files(run_.pre, run_.workspace, name),
        rx_names=rx_names,
    )
    if not run_.include_captures:
        summary.sessions.clear()
    run_.rep.note(summary=summary.to_json())
    return summary


def _pr_body(run_: _Contribution) -> str:
    """The PR body: ``--body`` as given, else the generated summary (+ logs)."""
    from canair.contribute import logs as L
    from canair.contribute import summary as S

    if run_.args.body:
        return run_.args.body
    summary = run_.summary
    rendered = S.render_markdown(summary, include_captures=run_.include_captures) if summary else ""
    logs = ""
    sessions = summary.sessions if summary else []
    # On by default, so a PR without new sessions gets no "nothing attached" section.
    if getattr(run_.args, "include_logs", False) and sessions:
        excerpt = L.collect(L.session_windows((s.date, s.start, s.end) for s in sessions))
        logs = L.render_markdown(excerpt)
        run_.rep.note(log_lines=len(excerpt.lines))
        if not run_.rep.json_mode:
            run_.rep.console.print(
                f"  attaching {len(excerpt.lines)} scrubbed diagnostics log line(s) to the PR"
            )
    return C.default_pr_body(
        run_.profile, include_captures=run_.include_captures, summary=rendered, logs=logs
    )


def _clear_leftovers(run_: _Contribution) -> None:
    """Back up and discard an earlier run's staged copy, which would block branching."""
    rep = run_.rep
    backup = C.backup_dir() / datetime.now().strftime("%Y%m%d-%H%M%S")
    paths = gates.leftovers(rep, run_.pre, run_.workspace, backup)
    if not paths:
        return
    C.backup_leftovers(run_.workspace, paths, backup)
    rep.note(leftovers_backup=str(backup))
    failed = next((s for s in C.discard_local_changes(run_.pre, run_.workspace) if not s.ok), None)
    if failed is not None:
        detail = failed.output
        rep.fail(
            "discarding workspace leftovers failed",
            detail=detail,
            human=lambda: rep.console.print(
                "  [red]could not discard the earlier contribution:[/red] "
                f"[dim]{escape(detail)}[/dim]\n"
                "  Delete the workspace and retry: "
                f"[cyan]rm -rf {escape(str(run_.workspace))}[/cyan]"
            ),
        )
    if not rep.json_mode:
        rep.console.print(
            f"  backed up and discarded {len(paths)} leftover file(s) → {escape(str(backup))}"
        )


def _make_hermetic(run_: _Contribution) -> None:
    """Discard leftovers in the managed workspace so only this run's copy is staged.

    Branching does not remove an uncommitted file an earlier run left behind (an
    interrupted or refused run stages a copy and never commits it), and
    ``commit_profile`` stages the whole profile directory — so without this, stale
    files ride along in the PR. Skipped for a user-supplied ``--repo-dir``, whose
    local work is theirs; that case is warned about by the workspace gate instead.
    """
    rep = run_.rep
    if not C.is_managed_workspace(run_.workspace):
        return
    failed = next((s for s in C.reset_workspace(run_.pre, run_.workspace) if not s.ok), None)
    if failed is None:
        return
    detail = failed.output
    rep.fail(
        "workspace reset failed",
        detail=detail,
        human=lambda: rep.console.print(
            "  [red]could not clean the staging workspace:[/red] "
            f"[dim]{escape(detail)}[/dim]\n"
            f"  Delete it and retry: [cyan]rm -rf {escape(str(run_.workspace))}[/cyan]"
        ),
    )


def _report_diff(run_: _Contribution) -> int:
    """``--diff``: show exactly what would be contributed, then stop."""
    rep, pre, workspace = run_.rep, run_.pre, run_.workspace
    diff = C.diff_profile(pre, workspace, run_.profile.name)

    def show() -> None:
        from rich.syntax import Syntax

        rep.console.print(
            f"\n  diff of [cyan]profiles/{escape(run_.profile.name)}/[/cyan] vs upstream:\n"
        )
        if diff.strip():
            rep.console.print(Syntax(diff, "diff", theme="ansi_dark", background_color="default"))
        else:
            rep.console.print("  [dim](no textual diff)[/dim]")
        if run_.rollback:
            rep.rollback_warning(run_.rollback)
        if run_.replaced:
            rep.replacement_warning(run_.replaced)
        rep.console.print("\n  [dim](diff only — nothing committed, pushed, or opened)[/dim]")
        rep.console.print("  Re-run without [cyan]--diff[/cyan] to contribute it.")

    return rep.done(
        show,
        diff=diff,
        rollback=rollback_json(run_.rollback),
        replaced_sessions=replaced_json(run_.replaced),
        pr_url=None,
    )


def _discard_staged(run_: _Contribution) -> None:
    """Drop whatever the run copied in but did not commit, however it ended.

    A ``--diff`` preview, a declined gate, ``--json`` without ``--yes`` or a failed
    commit all leave the copy uncommitted, and the next run would meet it as a
    leftover. The copy is derived from the source and upstream alone, so nothing
    is lost: a commit (including a ``--dry-run`` one) survives, since this resets
    to ``HEAD``. Managed workspace only: a ``--repo-dir`` may hold the user's own
    uncommitted work alongside the copy.
    """
    if not run_.staged or not C.is_managed_workspace(run_.workspace):
        return
    failed = next((s for s in C.discard_local_changes(run_.pre, run_.workspace) if not s.ok), None)
    if failed is not None:
        run_.rep.warn(f"could not clean up the staging workspace: {failed.output}")


def _submit(run_: _Contribution) -> int:
    """Commit the staged profile, then either stop (``--dry-run``) or open the PR."""
    rep = run_.rep
    _commit(run_)
    if run_.args.dry_run:
        body = _pr_body(run_)

        def prepared() -> None:
            rep.console.print("\n  pull request body:\n")
            rep.console.print(body, markup=False, highlight=False)
            rep.console.print(
                f"\n  [green]{glyphs.OK} Prepared[/green] branch "
                f"[cyan]{escape(run_.branch)}[/cyan] in {escape(str(run_.workspace))}"
            )
            rep.console.print("  [dim](dry run — nothing pushed; no PR opened)[/dim]")

        return rep.done(
            prepared,
            dry_run=True,
            findings=findings_json(run_.findings),
            pr_url=None,
            pr_body=body,
        )
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
                f"  [red]commit failed:[/red] [dim]{escape(commit.output)}[/dim]"
            ),
        )


def _push_and_open_pr(run_: _Contribution) -> int:
    """The point of no return: confirm, push the branch, open the pull request."""
    rep, pre, workspace, args = run_.rep, run_.pre, run_.workspace, run_.args

    if not rep.json_mode:
        rep.console.print(
            f"\n  Ready to push [cyan]{escape(run_.branch)}[/cyan] to {escape(run_.target)} "
            f"and open a pull request against [cyan]{C.UPSTREAM_REPO}[/cyan]."
        )
        rep.console.print(
            "  [dim](tip: `canair contribute --diff` shows the full change first)[/dim]"
        )
    rep.confirm(
        "Push and open the PR?",
        error="push not confirmed",
        after_decline="Nothing was pushed. Your branch is prepared locally in the workspace.",
    )

    if not rep.json_mode:
        rep.console.print(
            f"  pushing [cyan]{escape(run_.branch)}[/cyan] to {escape(run_.target)} …"
        )
    push = C.push_branch(pre, workspace, run_.branch)
    if not push.ok:
        rep.fail(
            "push failed",
            detail=push.output,
            human=lambda: rep.console.print(
                f"  [red]push failed:[/red] [dim]{escape(push.output)}[/dim]"
            ),
        )

    body = _pr_body(run_)

    # Re-running on the same day pushes to a branch that already has a PR, and the
    # push has just updated it — so that is a success, not a `gh pr create` error.
    # The branch now holds everything this run staged, so the body is regenerated.
    if existing := C.find_open_pr(pre, run_.branch):
        number, url = existing
        edit = C.update_pr_body(pre, number, body)
        if not edit.ok:
            rep.warn(f"could not update the PR description: {edit.output}")

        def updated() -> None:
            rep.console.print(
                f"\n  [green]{glyphs.OK} Updated pull request #{number}:[/green] {escape(url)}"
            )
            rep.console.print("  Thank you for contributing! 🎉")

        return _finish(
            run_,
            updated,
            dry_run=False,
            findings=findings_json(run_.findings),
            pr_url=url,
            pr_number=number,
            pr_updated=True,
        )

    pr = C.create_pr(
        pre,
        workspace,
        title=args.title
        or C.default_message(run_.profile, include_captures=run_.include_captures).splitlines()[0],
        body=body,
        head=C.pr_head(pre, run_.branch, run_.mode),
    )
    if not pr.ok:

        def explain() -> None:
            rep.console.print(f"  [red]opening the PR failed:[/red] [dim]{escape(pr.output)}[/dim]")
            rep.console.print("  Your branch was pushed — you can open the PR manually on GitHub.")

        rep.fail("pr create failed", detail=pr.output, human=explain)

    pr_url = pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else ""

    def celebrate() -> None:
        rep.console.print(f"\n  [green]{glyphs.OK} Pull request opened:[/green] {escape(pr_url)}")
        rep.console.print("  Thank you for contributing! 🎉")

    return _finish(
        run_,
        celebrate,
        dry_run=False,
        findings=findings_json(run_.findings),
        pr_url=pr_url,
        pr_updated=False,
    )


def _finish(run_: _Contribution, human, **extra: Any) -> int:
    """Report the open PR, then offer to tidy the source checkout it came from."""
    if not run_.rep.json_mode:
        human()
    return run_.rep.done(lambda: None, **extra, local_changes=_tidy_source(run_))


def _tidy_source(run_: _Contribution) -> dict[str, Any] | None:
    """Offer to drop the source checkout's changes that the contribution now holds.

    A profile recorded in a canair checkout keeps those changes uncommitted on its
    branch, so pulling the merged PR would conflict with them. Only changes the
    workspace's ``HEAD`` holds exactly are offered (:mod:`canair.contribute.source`);
    the rest are listed and left alone. Asked interactively, kept under ``--yes`` or
    ``--json`` unless ``--discard-local`` says otherwise.
    """
    from canair.contribute import source as src

    rep = run_.rep
    changes = src.find_changes(run_.pre, run_.profile.root, run_.profile.name, run_.workspace)
    if changes is None or not changes.held:
        return None
    report: dict[str, Any] = {
        "checkout": str(changes.checkout),
        "paths": changes.held,
        "not_contributed": changes.not_held,
        "action": "kept",
        "backup": None,
    }
    backup = C.backup_dir() / f"{datetime.now():%Y%m%d-%H%M%S}-source"
    choice = getattr(run_.args, "local_changes", None)
    if choice is None and not (rep.json_mode or rep.yes) and sys.stdin.isatty():
        rep.show_source_changes(changes, run_.profile.name, backup)
        choice = "discard" if confirm("Discard them?") else "keep"
    if choice != "discard":
        if not rep.json_mode and getattr(run_.args, "local_changes", None) is None:
            rep.console.print(
                "  Kept. Drop them before pulling the merged PR, or re-run with "
                "[cyan]--discard-local[/cyan]."
            )
        return report

    failed = src.discard(run_.pre, changes, backup)
    report["backup"] = str(backup)
    if failed:
        rep.warn(f"could not discard the contributed changes: {failed[0].output}")
        report["action"] = "failed"
        return report
    report["action"] = "discarded"
    if not rep.json_mode:
        rep.console.print(
            f"  [green]{glyphs.OK}[/green] Discarded {len(changes.held)} change(s) from "
            f"{escape(str(changes.checkout))}; backup → {escape(str(backup))}"
        )
    return report


# --- helpers ----------------------------------------------------------------
