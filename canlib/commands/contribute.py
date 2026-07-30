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
        help="Contribute definitions only (ecus/, profile.yaml, states, buses, signals)",
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

    # 2. PII pre-flight.
    findings = pii.scan_profile(profile, include_captures=include_captures)
    findings_json = [{"location": f.location, "kind": f.kind, "detail": f.detail} for f in findings]
    if findings and not json_mode:
        c.print(
            f"\n[yellow]⚠ {len(findings)} possible privacy issue(s)[/yellow] "
            "— the tree is public, so review before sharing:\n"
        )
        for f in findings:
            c.print(f"  [yellow]•[/yellow] {f.location}  [dim]({f.detail})[/dim]")
        c.print("")
        if not _confirm("Contribute anyway?", args.yes, json_mode=json_mode):
            c.print("  Aborted — nothing was contributed.")
            return _CANNOT
    elif findings and json_mode and not args.yes:
        return _emit_json(
            {
                "ok": False,
                "cannot": True,
                "profile": profile.name,
                "findings": findings_json,
                "error": "possible PII found; re-run with --yes to proceed",
            }
        )

    # 3. Environment: gh + git + authenticated.
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

    # 4. Size guard on captures.
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

    # 5. Workspace: managed fork clone (or an explicit --repo-dir).
    workspace = Path(args.repo_dir).expanduser() if args.repo_dir else C.workspace_dir()
    if not json_mode:
        c.print(f"\n  workspace: [dim]{workspace}[/dim]")

    if not args.repo_dir:
        if not json_mode:
            c.print("  syncing your fork (first run forks + clones — may take a moment) …")
        steps = C.ensure_fork_clone(pre, workspace)
        if steps and not steps[-1].ok:
            failed = steps[-1]
            if json_mode:
                return _emit_json(
                    {"ok": False, "error": "fork/clone failed", "detail": failed.output}
                )
            c.print("  [red]failed to prepare the fork clone:[/red]")
            c.print(f"  [dim]{failed.output}[/dim]")
            c.print("\n" + C.manual_instructions(profile.name, branch))
            return _FAILED

    # 6. Branch, copy, commit.
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

    # 7. Push + open the PR.
    if not json_mode:
        c.print(f"  pushing [cyan]{branch}[/cyan] to your fork …")
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
    head = C.fork_head(pre, workspace, branch)
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
