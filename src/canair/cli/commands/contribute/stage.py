"""Staging for ``canair contribute``: sync the workspace, copy the profile in, summarize."""

from __future__ import annotations

from datetime import datetime

from canair.contribute.checks import definition_rollback
from canair.contribute.copy import copy_profile
from canair.contribute.git import (
    base_reader,
    changed_definition_files,
    diff_profile,
    has_changes,
    start_branch,
)
from canair.contribute.github import manual_instructions
from canair.contribute.workspace import (
    backup_dir,
    backup_leftovers,
    discard_local_changes,
    ensure_workspace,
    is_managed_workspace,
    reset_workspace,
)
from canair.profile import active
from canair.render import glyphs
from canair.render.console import escape

from . import gates
from .report import replaced_json, rollback_json
from .state import Contribution
from .tidy import tidy_source


def stage_contribution(run_: Contribution) -> int | None:
    """Sync the workspace, branch, and copy the profile in.

    Returns an exit code when the run is already complete (the profile matches
    upstream, so there is nothing to contribute), else ``None``.
    """
    rep, pre, workspace = run_.rep, run_.pre, run_.workspace

    if not rep.json_mode:
        rep.console.print(f"\n  reading profile from: [dim]{escape(str(run_.profile.root))}[/dim]")
        rep.console.print(f"  staging in workspace: [dim]{escape(str(workspace))}[/dim]")
        rep.console.print("  syncing (first run clones — may take a moment) …")

    steps, mode, ok = ensure_workspace(pre, workspace)
    run_.mode = mode
    rep.note(mode=mode)
    if not ok:
        failed = steps[-1] if steps else None
        detail = failed.output if failed else "unknown error"

        def explain() -> None:
            rep.console.print("  [red]failed to prepare the workspace:[/red]")
            rep.console.print(f"  [dim]{escape(detail)}[/dim]")
            rep.console.print("\n" + escape(manual_instructions(run_.profile.name, run_.branch)))

        rep.fail("workspace prep failed", detail=detail, human=explain)
    if not rep.json_mode:
        rep.console.print(
            f"  mode: [cyan]{escape(mode)}[/cyan] (will push to {escape(run_.target)})"
        )

    _clear_leftovers(run_)

    br = start_branch(pre, workspace, run_.branch)
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

    copy_profile(
        run_.profile,
        workspace,
        include_captures=run_.include_captures,
        warn=rep.warn,
        replaced=run_.replaced.append,
    )

    if not has_changes(pre, workspace, run_.profile.name):
        msg = "No changes to contribute — the upstream profile already matches yours."
        if not rep.json_mode:
            rep.console.print(f"\n  [green]{glyphs.OK} {msg}[/green]")
        local = None if run_.args.diff or run_.args.dry_run else tidy_source(run_)
        return rep.done(lambda: None, no_changes=True, message=msg, local_changes=local)

    # Curated definitions normally only grow, so a diff that *removes* committed
    # upstream lines suggests a stale source. Computed here because both --diff
    # and the privacy gate report it.
    run_.rollback = definition_rollback(pre, workspace, run_.profile.name)
    run_.summary = summarize(run_)
    return None


def summarize(run_: Contribution):
    """What the staged contribution adds, for the PR body and ``--json``."""
    from canair.contribute import summary as S

    try:
        from canair.ecus.registry import build_rx_index

        rx_names = build_rx_index(profile=active())
    except Exception:
        rx_names = {}
    name = run_.profile.name
    summary = S.summarize(
        run_.workspace / "profiles" / name / "captures",
        profile_name=name,
        base_reader=base_reader(run_.pre, run_.workspace),
        definition_files=changed_definition_files(run_.pre, run_.workspace, name),
        rx_names=rx_names,
    )
    if not run_.include_captures:
        summary.sessions.clear()
    run_.rep.note(summary=summary.to_json())
    return summary


def _clear_leftovers(run_: Contribution) -> None:
    """Back up and discard an earlier run's staged copy, which would block branching."""
    rep = run_.rep
    backup = backup_dir() / datetime.now().strftime("%Y%m%d-%H%M%S")
    paths = gates.leftovers(rep, run_.pre, run_.workspace, backup)
    if not paths:
        return
    backup_leftovers(run_.workspace, paths, backup)
    rep.note(leftovers_backup=str(backup))
    failed = next((s for s in discard_local_changes(run_.pre, run_.workspace) if not s.ok), None)
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


def _make_hermetic(run_: Contribution) -> None:
    """Discard leftovers in the managed workspace so only this run's copy is staged.

    Branching does not remove an uncommitted file an earlier run left behind (an
    interrupted or refused run stages a copy and never commits it), and
    ``commit_profile`` stages the whole profile directory — so without this, stale
    files ride along in the PR. Skipped for a user-supplied ``--repo-dir``, whose
    local work is theirs; that case is warned about by the workspace gate instead.
    """
    rep = run_.rep
    if not is_managed_workspace(run_.workspace):
        return
    failed = next((s for s in reset_workspace(run_.pre, run_.workspace) if not s.ok), None)
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


def report_diff(run_: Contribution) -> int:
    """``--diff``: show exactly what would be contributed, then stop."""
    rep, pre, workspace = run_.rep, run_.pre, run_.workspace
    diff = diff_profile(pre, workspace, run_.profile.name)

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


def discard_staged(run_: Contribution) -> None:
    """Drop whatever the run copied in but did not commit, however it ended.

    A ``--diff`` preview, a declined gate, ``--json`` without ``--yes`` or a failed
    commit all leave the copy uncommitted, and the next run would meet it as a
    leftover. The copy is derived from the source and upstream alone, so nothing
    is lost: a commit (including a ``--dry-run`` one) survives, since this resets
    to ``HEAD``. Managed workspace only: a ``--repo-dir`` may hold the user's own
    uncommitted work alongside the copy.
    """
    if not run_.staged or not is_managed_workspace(run_.workspace):
        return
    failed = next((s for s in discard_local_changes(run_.pre, run_.workspace) if not s.ok), None)
    if failed is not None:
        run_.rep.warn(f"could not clean up the staging workspace: {failed.output}")
