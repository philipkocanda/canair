"""``canair contribute`` — open a pull request for the active profile.

A one-command path to share a reverse-engineered profile (definitions and
captures) upstream, without the manual fork/clone/branch/push dance. It works
regardless of where the profile is stored (bundled repo, ``~/.config`` user dir,
or a ``--path`` bundle): the profile is copied into a managed fork checkout and a
PR is opened via the GitHub CLI (``gh``).

See the module docstring of :mod:`canlib.contribute` for the orchestration and
:mod:`canlib.pii` for the privacy pre-flight.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from datetime import date

from .. import contribute as C
from .. import pii
from ..profile import ProfileError, active

NAME = "contribute"

# Warn before contributing a captures/ dir larger than this (bytes).
_CAPTURES_WARN_BYTES = 25 * 1024 * 1024

_OK = 0
_FAILED = 1
_CANNOT = 2


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=["share"],
        help="Open a pull request contributing the active profile upstream",
        description=__doc__,
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


def _confirm(prompt: str, yes: bool, *, json_mode: bool) -> bool:
    """Ask to proceed unless ``--yes``; non-interactive without ``--yes`` aborts."""
    if yes:
        return True
    if json_mode or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _print_rollback_warning(c, rollback: list[tuple[str, int]]) -> None:
    """Warn that the contribution removes committed upstream definition lines."""
    c.print(
        "\n[yellow]⚠ This contribution removes committed upstream definition "
        "lines[/yellow] — curated definitions normally only grow, so if your source\n"
        "  is stale this would revert work already merged upstream:\n"
    )
    for path, removed in rollback:
        c.print(f"  [yellow]•[/yellow] {path}  [dim](−{removed} line(s))[/dim]")
    c.print(
        "\n  If this is a deliberate cleanup, proceed. Otherwise sync your source "
        "first\n  (e.g. [cyan]git pull[/cyan], and run [cyan]uv run canair[/cyan] "
        "from your checkout).\n"
    )


def _validate(profile) -> tuple[bool, str]:
    """Run ``validate all`` against ``profile``; return (ok, captured_output)."""
    from .validate import run as validate_run

    ns = argparse.Namespace(target="all", files=None, stats=False, strict=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = validate_run(ns)
    return rc == 0, buf.getvalue()


def _emit_json(payload: dict) -> int:
    import json

    print(json.dumps(payload, indent=2))
    return _OK if payload.get("ok") else (_CANNOT if payload.get("cannot") else _FAILED)


def run(args) -> int:
    from pathlib import Path

    from rich.console import Console

    c = Console()
    json_mode: bool = args.json
    include_captures: bool = args.captures

    try:
        profile = active()
    except ProfileError as e:
        if json_mode:
            return _emit_json({"ok": False, "cannot": True, "error": str(e)})
        c.print(f"[red]error:[/red] {e}")
        return _CANNOT

    branch = args.branch or f"contribute/{profile.name}-{date.today():%Y%m%d}"

    # 0. Source sanity: warn if the profile is an installed snapshot, not a
    #    working checkout. A bare `canair` resolves the profile from the frozen
    #    site-packages copy, which can be behind the checkout (and ahead on
    #    captures from bare --save runs) — contributing it silently reverts work.
    snapshot = C.installed_snapshot_kind(profile.root)
    if snapshot:
        if json_mode and not args.yes:
            return _emit_json(
                {
                    "ok": False,
                    "cannot": True,
                    "profile": profile.name,
                    "installed_snapshot": snapshot,
                    "source": str(profile.root),
                    "error": (
                        f"profile resolved from an installed {snapshot} snapshot, not a "
                        "checkout; it may be stale — re-run with --yes to proceed"
                    ),
                }
            )
        if not json_mode:
            c.print(
                f"\n[yellow]⚠ This profile was read from an installed {snapshot} "
                f"snapshot[/yellow], not your working checkout:\n  [dim]{profile.root}[/dim]\n"
                "  That copy is frozen at install time — it can be behind your checkout\n"
                "  (and ahead on captures from bare `--save` runs), so contributing it may\n"
                "  revert upstream work. Prefer running [cyan]uv run canair contribute[/cyan] "
                "from your repo checkout.\n"
            )
            if not _confirm(
                "Contribute from the installed snapshot anyway?", args.yes, json_mode=json_mode
            ):
                c.print("  Aborted — nothing was contributed.")
                return _CANNOT

    # 1. Validate — refuse to contribute a broken profile.
    ok, report = _validate(profile)
    if not ok:
        if json_mode:
            return _emit_json(
                {"ok": False, "cannot": True, "profile": profile.name, "error": "validation failed"}
            )
        c.print("[red]Profile failed validation — fix it before contributing:[/red]\n")
        print(report)
        c.print("  Run `canair validate all` for details.")
        return _CANNOT

    # 2. Environment: gh + git + authenticated.
    pre = C.preflight()
    if not pre.gh:
        if json_mode:
            return _emit_json({"ok": False, "cannot": True, "error": "gh not found"})
        c.print("\n[yellow]The GitHub CLI (`gh`) is not installed.[/yellow]\n")
        c.print(C.gh_install_hint())
        c.print("\n" + C.manual_instructions(profile.name, branch))
        return _CANNOT
    if not pre.git:
        if json_mode:
            return _emit_json({"ok": False, "cannot": True, "error": "git not found"})
        c.print("[yellow]`git` not found on PATH.[/yellow]")
        return _CANNOT
    if not pre.authenticated:
        if json_mode:
            return _emit_json({"ok": False, "cannot": True, "error": "gh not authenticated"})
        c.print("\n[yellow]You're not signed in to GitHub.[/yellow] Run:\n")
        c.print("    gh auth login\n")
        c.print("  then re-run `canair contribute`.")
        return _CANNOT

    # 3. Size guard on captures (source-side; fail fast before the clone).
    if include_captures:
        size = C.dir_size(profile.captures_dir)
        if size > _CAPTURES_WARN_BYTES and not json_mode:
            mb = size / (1024 * 1024)
            c.print(
                f"\n[yellow]captures/ is large (~{mb:.0f} MB).[/yellow] "
                "Consider `--no-captures` or trimming it first."
            )
            if not _confirm("Include captures anyway?", args.yes, json_mode=json_mode):
                c.print("  Aborted — nothing was contributed.")
                return _CANNOT

    # 4. Workspace: fork clone, direct upstream clone, or an explicit --repo-dir.
    workspace = Path(args.repo_dir).expanduser() if args.repo_dir else C.workspace_dir()
    if not json_mode:
        c.print(f"\n  reading profile from: [dim]{profile.root}[/dim]")
        c.print(f"  staging in workspace: [dim]{workspace}[/dim]")
        c.print("  syncing (first run clones — may take a moment) …")
    steps, mode, ok = C.ensure_workspace(pre, workspace)
    if not ok:
        failed = steps[-1] if steps else None
        detail = failed.output if failed else "unknown error"
        if json_mode:
            return _emit_json({"ok": False, "error": "workspace prep failed", "detail": detail})
        c.print("  [red]failed to prepare the workspace:[/red]")
        c.print(f"  [dim]{detail}[/dim]")
        c.print("\n" + C.manual_instructions(profile.name, branch))
        return _FAILED
    if not json_mode:
        where = C.UPSTREAM_REPO if mode == C.MODE_DIRECT else "your fork"
        c.print(f"  mode: [cyan]{mode}[/cyan] (will push to {where})")

    # 5. Branch + copy the profile in.
    br = C.start_branch(pre, workspace, branch)
    if not br.ok:
        if json_mode:
            return _emit_json({"ok": False, "error": "branch failed", "detail": br.output})
        c.print(f"  [red]could not create branch {branch}:[/red] [dim]{br.output}[/dim]")
        return _FAILED

    C.copy_profile(profile, workspace, include_captures=include_captures)

    if not C.has_changes(pre, workspace, profile.name):
        msg = "No changes to contribute — the upstream profile already matches yours."
        if json_mode:
            return _emit_json(
                {"ok": True, "profile": profile.name, "no_changes": True, "message": msg}
            )
        c.print(f"\n  [green]{msg}[/green]")
        return _OK

    # 5b. Staleness check: does this contribution *remove* committed upstream
    #     lines from curated definitions? Those normally only grow, so a
    #     rollback signals the source is likely stale (would revert upstream).
    rollback = C.definition_rollback(pre, workspace, profile.name)
    rollback_json = [{"path": p, "removed_lines": n} for p, n in rollback]

    # 5c. --diff: show exactly what would be contributed, then stop.
    if args.diff:
        diff = C.diff_profile(pre, workspace, profile.name)
        if json_mode:
            return _emit_json(
                {
                    "ok": True,
                    "profile": profile.name,
                    "branch": branch,
                    "mode": mode,
                    "include_captures": include_captures,
                    "workspace": str(workspace),
                    "source": str(profile.root),
                    "diff": diff,
                    "rollback": rollback_json,
                    "pr_url": None,
                }
            )
        from rich.syntax import Syntax

        c.print(f"\n  diff of [cyan]profiles/{profile.name}/[/cyan] vs upstream:\n")
        if diff.strip():
            c.print(Syntax(diff, "diff", theme="ansi_dark", background_color="default"))
        else:
            c.print("  [dim](no textual diff)[/dim]")
        if rollback:
            _print_rollback_warning(c, rollback)
        c.print("\n  [dim](diff only — nothing committed, pushed, or opened)[/dim]")
        c.print("  Re-run without [cyan]--diff[/cyan] to contribute it.")
        return _OK

    # 6. PII pre-flight — scoped to what THIS contribution adds/changes vs
    #    upstream (already-committed captures are not re-flagged).
    findings = pii.scan_contribution(
        profile,
        workspace / "profiles" / profile.name / "captures",
        include_captures=include_captures,
        base_reader=C.base_reader(pre, workspace),
    )
    findings_json = [{"location": f.location, "kind": f.kind, "detail": f.detail} for f in findings]
    if findings:
        if json_mode and not args.yes:
            return _emit_json(
                {
                    "ok": False,
                    "cannot": True,
                    "profile": profile.name,
                    "findings": findings_json,
                    "error": "possible PII in the contribution; re-run with --yes to proceed",
                }
            )
        if not json_mode:
            c.print(
                f"\n[yellow]⚠ {len(findings)} possible privacy issue(s) in this "
                "contribution[/yellow] — the tree is public, so review before sharing:\n"
            )
            for f in findings:
                c.print(f"  [yellow]•[/yellow] {f.location}  [dim]({f.detail})[/dim]")
            c.print("")
            if not _confirm("Contribute anyway?", args.yes, json_mode=json_mode):
                c.print("  Aborted — nothing was contributed.")
                return _CANNOT

    # 6b. Rollback pre-flight — the contribution removes committed upstream
    #     definition lines (likely a stale source reverting upstream work).
    if rollback:
        if json_mode and not args.yes:
            return _emit_json(
                {
                    "ok": False,
                    "cannot": True,
                    "profile": profile.name,
                    "rollback": rollback_json,
                    "error": (
                        "this contribution removes committed upstream definition lines "
                        "(source may be stale); re-run with --yes to proceed"
                    ),
                }
            )
        if not json_mode:
            _print_rollback_warning(c, rollback)
            if not _confirm("Contribute this rollback anyway?", args.yes, json_mode=json_mode):
                c.print("  Aborted — nothing was contributed.")
                return _CANNOT

    # 7. Commit.
    commit = C.commit_profile(
        pre,
        workspace,
        profile.name,
        args.title or C.default_message(profile, include_captures=include_captures),
    )
    if not commit.ok:
        if json_mode:
            return _emit_json({"ok": False, "error": "commit failed", "detail": commit.output})
        c.print(f"  [red]commit failed:[/red] [dim]{commit.output}[/dim]")
        return _FAILED

    if args.dry_run:
        result = {
            "ok": True,
            "profile": profile.name,
            "branch": branch,
            "mode": mode,
            "include_captures": include_captures,
            "workspace": str(workspace),
            "dry_run": True,
            "findings": findings_json,
            "pr_url": None,
        }
        if json_mode:
            return _emit_json(result)
        c.print(f"\n  [green]✓ Prepared[/green] branch [cyan]{branch}[/cyan] in {workspace}")
        c.print("  [dim](dry run — nothing pushed; no PR opened)[/dim]")
        return _OK

    # 8. Confirm, then push + open the PR.
    where = C.UPSTREAM_REPO if mode == C.MODE_DIRECT else "your fork"
    if not json_mode:
        c.print(
            f"\n  Ready to push [cyan]{branch}[/cyan] to {where} and open a pull "
            f"request against [cyan]{C.UPSTREAM_REPO}[/cyan]."
        )
        c.print("  [dim](tip: `canair contribute --diff` shows the full change first)[/dim]")
    if not _confirm("Push and open the PR?", args.yes, json_mode=json_mode):
        if json_mode:
            return _emit_json(
                {
                    "ok": False,
                    "cannot": True,
                    "profile": profile.name,
                    "branch": branch,
                    "error": "push not confirmed (pass --yes to proceed non-interactively)",
                }
            )
        c.print("  Aborted — nothing was pushed. Your branch is prepared locally in the workspace.")
        return _CANNOT

    if not json_mode:
        c.print(f"  pushing [cyan]{branch}[/cyan] to {where} …")
    push = C.push_branch(pre, workspace, branch)
    if not push.ok:
        if json_mode:
            return _emit_json({"ok": False, "error": "push failed", "detail": push.output})
        c.print(f"  [red]push failed:[/red] [dim]{push.output}[/dim]")
        return _FAILED

    title = (
        args.title or C.default_message(profile, include_captures=include_captures).splitlines()[0]
    )
    body = args.body or C.default_pr_body(profile, include_captures=include_captures)
    head = C.pr_head(pre, branch, mode)
    pr = C.create_pr(pre, workspace, title=title, body=body, head=head)
    if not pr.ok:
        if json_mode:
            return _emit_json({"ok": False, "error": "pr create failed", "detail": pr.output})
        c.print(f"  [red]opening the PR failed:[/red] [dim]{pr.output}[/dim]")
        c.print("  Your branch was pushed — you can open the PR manually on GitHub.")
        return _FAILED

    pr_url = pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else ""
    if json_mode:
        return _emit_json(
            {
                "ok": True,
                "profile": profile.name,
                "branch": branch,
                "mode": mode,
                "include_captures": include_captures,
                "workspace": str(workspace),
                "dry_run": False,
                "findings": findings_json,
                "pr_url": pr_url,
            }
        )
    c.print(f"\n  [green]✓ Pull request opened:[/green] {pr_url}")
    c.print("  Thank you for contributing! 🎉")
    return _OK
