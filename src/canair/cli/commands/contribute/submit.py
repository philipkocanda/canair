"""Submitting for ``canair contribute``: commit, push, open or update the pull request."""

from __future__ import annotations

from typing import Any

from canair.contribute.git import commit_profile, push_branch
from canair.contribute.github import (
    create_pr,
    default_message,
    default_pr_body,
    find_open_pr,
    pr_head,
    update_pr_body,
)
from canair.core.paths import GITHUB_REPO
from canair.render import glyphs
from canair.render.console import escape

from .report import findings_json
from .state import Contribution
from .tidy import tidy_source


def pr_body(run_: Contribution) -> str:
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
    return default_pr_body(
        run_.profile, include_captures=run_.include_captures, summary=rendered, logs=logs
    )


def submit(run_: Contribution) -> int:
    """Commit the staged profile, then either stop (``--dry-run``) or open the PR."""
    rep = run_.rep
    _commit(run_)
    if run_.args.dry_run:
        body = pr_body(run_)

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


def _commit(run_: Contribution) -> None:
    """Commit the copied-in profile onto the contribution branch."""
    rep, pre, workspace, args = run_.rep, run_.pre, run_.workspace, run_.args

    commit = commit_profile(
        pre,
        workspace,
        run_.profile.name,
        args.title or default_message(run_.profile, include_captures=run_.include_captures),
    )
    if not commit.ok:
        rep.fail(
            "commit failed",
            detail=commit.output,
            human=lambda: rep.console.print(
                f"  [red]commit failed:[/red] [dim]{escape(commit.output)}[/dim]"
            ),
        )


def _push_and_open_pr(run_: Contribution) -> int:
    """The point of no return: confirm, push the branch, open the pull request."""
    rep, pre, workspace, args = run_.rep, run_.pre, run_.workspace, run_.args

    if not rep.json_mode:
        rep.console.print(
            f"\n  Ready to push [cyan]{escape(run_.branch)}[/cyan] to {escape(run_.target)} "
            f"and open a pull request against [cyan]{GITHUB_REPO}[/cyan]."
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
    push = push_branch(pre, workspace, run_.branch)
    if not push.ok:
        rep.fail(
            "push failed",
            detail=push.output,
            human=lambda: rep.console.print(
                f"  [red]push failed:[/red] [dim]{escape(push.output)}[/dim]"
            ),
        )

    body = pr_body(run_)

    # Re-running on the same day pushes to a branch that already has a PR, and the
    # push has just updated it — so that is a success, not a `gh pr create` error.
    # The branch now holds everything this run staged, so the body is regenerated.
    if existing := find_open_pr(pre, run_.branch):
        number, url = existing
        edit = update_pr_body(pre, number, body)
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

    pr = create_pr(
        pre,
        workspace,
        title=args.title
        or default_message(run_.profile, include_captures=run_.include_captures).splitlines()[0],
        body=body,
        head=pr_head(pre, run_.branch, run_.mode),
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


def _finish(run_: Contribution, human, **extra: Any) -> int:
    """Report the open PR, then offer to tidy the source checkout it came from."""
    if not run_.rep.json_mode:
        human()
    return run_.rep.done(lambda: None, **extra, local_changes=tidy_source(run_))
