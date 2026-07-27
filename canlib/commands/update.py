"""``canair update`` — update the CLI from its git-clone install.

canair is installed from a git clone (``git clone`` + ``uv tool install .``), so
updating means: check out the latest **release tag** in the clone, then reinstall
the tool from it. This command locates that source clone (via uv's tool receipt,
falling back to the package's own repo root), reports the current vs latest
released version with a changelog link, and — after confirmation — runs
``git fetch --tags`` + ``git checkout <tag>`` + ``uv tool install <clone> --reinstall``.

Checking out the advertised release tag (rather than fast-forwarding a branch to
its HEAD) means the installed code is exactly the released version — never
whatever unreleased commits happen to sit on ``main``.

It never mutates anything without an interactive confirmation or ``--yes``, and
degrades gracefully to printing manual instructions when it can't find a git
clone or ``uv`` (e.g. a pip/editable install). ``--check`` reports only;
``--json`` emits a machine-readable summary.

It also reports the **install context**: which copy is running — the repo
working tree (``uv run`` / dev checkout) vs the ``uv tool install`` snapshot
(bare ``canair``) — the clone's current git HEAD (branch name, e.g. ``main``, or
``detached at <tag>`` after an update), and warns when the installed tool copy's
version has drifted out of sync with the source clone's ``pyproject.toml`` (so a
bare ``canair`` would run different code than ``uv run canair``).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from ..install_context import describe as describe_install
from ..update_check import (
    CHANGELOG_URL,
    _is_newer,
    fetch_latest_release,
    write_cache,
)

NAME = "update"

# Exit codes.
_OK = 0
_FAILED = 1
_CANNOT = 2


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Update canair from its git-clone install (checkout release tag + reinstall)",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair update            # check, confirm, then checkout release tag + reinstall
  canair update --check    # report current/latest + changelog, change nothing
  canair update --yes      # update without the confirmation prompt (automation)
  canair update --json     # machine-readable version/clone summary
""",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only report the current/latest version and changelog; make no changes",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (checkout release tag + reinstall)",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.set_defaults(func=run)
    return parser


def _find_clone_dir() -> Path | None:
    """Locate the source git clone canair was installed from.

    Preference order:
      1. uv's tool receipt (``~/.local/share/uv/tools/canair/uv-receipt.toml``),
         which records the ``directory`` a ``uv tool install .`` came from.
      2. The package's own repo root (``canlib.constants.SCRIPT_DIR``), covering
         editable / ``uv run`` dev checkouts.
    Returns the first that is an existing git working tree, else ``None``.
    """
    candidates: list[Path] = []
    receipt = _uv_receipt_directory()
    if receipt is not None:
        candidates.append(receipt)
    try:
        from ..constants import SCRIPT_DIR

        candidates.append(Path(SCRIPT_DIR))
    except Exception:
        pass
    for path in candidates:
        if (path / ".git").exists():
            return path
    return None


def _uv_receipt_directory() -> Path | None:
    """Read the source directory from uv's tool receipt, if present."""
    import os

    data_home = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    receipt = Path(data_home) / "uv" / "tools" / "canair" / "uv-receipt.toml"
    try:
        import tomllib

        parsed = tomllib.loads(receipt.read_text())
    except (OSError, ValueError, ModuleNotFoundError):
        return None
    for req in parsed.get("tool", {}).get("requirements", []):
        directory = req.get("directory") if isinstance(req, dict) else None
        if directory:
            return Path(directory)
    return None


def _git(clone: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(clone), *args],
        capture_output=True,
        text=True,
    )


def _git_dirty(clone: Path) -> bool:
    """True when the clone has uncommitted changes (don't clobber contributor work)."""
    res = _git(clone, "status", "--porcelain")
    return res.returncode == 0 and bool(res.stdout.strip())


def _git_head(clone: Path) -> str | None:
    """Describe the clone's current HEAD for display.

    Returns the branch name when on a branch (e.g. ``main``), or a
    ``detached at <tag-or-commit>`` string when in detached-HEAD state (which is
    where ``canair update`` leaves the clone after checking out a release tag).
    ``None`` if git can't be queried.
    """
    branch = _git(clone, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch is not None and branch.returncode == 0 and branch.stdout.strip():
        return branch.stdout.strip()
    # Detached HEAD — prefer an exact tag name, else the short commit.
    tag = _git(clone, "describe", "--tags", "--exact-match", "HEAD")
    if tag is not None and tag.returncode == 0 and tag.stdout.strip():
        return f"detached at {tag.stdout.strip()}"
    commit = _git(clone, "rev-parse", "--short", "HEAD")
    if commit is not None and commit.returncode == 0 and commit.stdout.strip():
        return f"detached at {commit.stdout.strip()}"
    return None


def _manual_instructions(clone: Path | None, tag: str | None = None) -> str:
    loc = str(clone) if clone else "<your canair clone>"
    ref = tag or "<latest release tag>"
    return (
        "  To update manually:\n"
        f"    cd {loc}\n"
        "    git fetch --tags\n"
        f"    git checkout {ref}\n"
        "    uv tool install . --reinstall\n"
    )


def _origin_label(origin: str) -> str:
    return {
        "repo": "repo working tree (uv run / dev checkout)",
        "uv-tool": "uv tool install copy (bare `canair`)",
        "other": "other install (pip / site-packages)",
    }.get(origin, origin)


def _print_install_context(c, install: dict) -> None:
    """Render which copy is running and whether the tool copy is out of sync."""
    origin = install["running_origin"]
    c.print(f"  running:  {_origin_label(origin)}")
    c.print(f"  [dim]  from {install['running_package_dir']}[/dim]")

    tool_version = install["tool_version"]
    clone_version = install["clone_version"]
    if tool_version is not None:
        c.print(f"  installed `canair`: {tool_version}  [dim](uv tool copy)[/dim]")

    if install["out_of_sync"]:
        c.print(
            "\n  [yellow]⚠ out of sync:[/yellow] the installed `canair` "
            f"([bold]{tool_version}[/bold]) differs from the source clone "
            f"([bold]{clone_version}[/bold])."
        )
        if origin == "repo":
            c.print(
                "  [dim]`uv run canair` runs the clone; a bare `canair` runs the older "
                "installed copy.[/dim]"
            )
        else:
            c.print(
                "  [dim]a bare `canair` runs this installed copy; `uv run canair` in the "
                "clone runs newer code.[/dim]"
            )
        c.print(
            "  [dim]run `canair update` (or `uv tool install <clone> --reinstall`) to sync.[/dim]"
        )
    c.print("")


def run(args) -> int:
    from .. import __version__

    clone = _find_clone_dir()
    install = describe_install(clone)
    release = fetch_latest_release()
    latest = release["tag"] if release else None
    changelog = (release or {}).get("url") or CHANGELOG_URL

    clone_head = _git_head(clone) if clone else None
    update_available = _is_newer(latest, __version__)

    if args.json:
        import json

        print(
            json.dumps(
                {
                    "current": __version__,
                    "latest": latest,
                    "update_available": update_available,
                    "clone_dir": str(clone) if clone else None,
                    "clone_head": clone_head,
                    "changelog_url": changelog,
                    "install": install,
                },
                indent=2,
            )
        )
        return _OK

    from rich.console import Console

    c = Console()
    c.print(f"\n  [bold]canair[/bold]  current: {__version__}")
    if latest is None:
        c.print("  [yellow]could not reach GitHub to check the latest release[/yellow]")
        c.print("  [dim](offline, or GitHub unreachable — a release tag is needed to update)[/dim]")
    elif update_available:
        c.print(f"          latest:  [green]{latest}[/green]  (update available)")
    else:
        c.print(f"          latest:  {latest}  [green](up to date)[/green]")
    c.print(f"  changelog: [dim]{changelog}[/dim]\n")

    _print_install_context(c, install)

    if clone is not None and clone_head is not None:
        c.print(f"  clone:    {clone}")
        c.print(f"  on:       [cyan]{clone_head}[/cyan]\n")

    # Refresh the cache so any pending auto-notice clears after an explicit check.
    write_cache(latest, changelog)

    if args.check:
        return _OK

    if latest is not None and not update_available:
        c.print("  Already up to date. Nothing to do.\n")
        return _OK

    if clone is None:
        c.print("  [yellow]Couldn't locate a git clone to update from.[/yellow]")
        c.print(_manual_instructions(None, latest))
        return _CANNOT

    if latest is None:
        c.print(
            "  [yellow]Couldn't determine the latest release tag (GitHub unreachable).[/yellow]\n"
            "  Refusing to guess a version to check out. Try again when online, or\n"
            "  update manually:\n"
        )
        c.print(_manual_instructions(clone, None))
        return _CANNOT

    if _git_dirty(clone):
        c.print(
            f"  [yellow]The clone at {clone} has uncommitted changes.[/yellow]\n"
            "  Refusing to touch it so your work isn't clobbered. Commit or stash first,\n"
            "  then re-run, or update manually:\n"
        )
        c.print(_manual_instructions(clone, latest))
        return _CANNOT

    uv = shutil.which("uv")
    if uv is None:
        c.print("  [yellow]`uv` not found on PATH — can't reinstall automatically.[/yellow]")
        c.print(_manual_instructions(clone, latest))
        return _CANNOT

    c.print(f"  This will check out [bold]{latest}[/bold] in the clone at [bold]{clone}[/bold]:")
    c.print(f"    git fetch --tags  &&  git checkout {latest}  &&  uv tool install . --reinstall\n")

    if not args.yes:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            c.print("  Non-interactive; pass --yes to proceed. Nothing changed.\n")
            return _CANNOT
        try:
            answer = input("  Proceed? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            answer = ""
        if answer not in ("y", "yes"):
            c.print("  Aborted. Nothing changed.\n")
            return _CANNOT

    c.print("\n  Fetching tags …")
    fetch = _git(clone, "fetch", "--tags", "--force")
    if fetch.returncode != 0:
        c.print("  [red]git fetch failed:[/red]")
        c.print(f"  [dim]{(fetch.stderr or fetch.stdout).strip()}[/dim]\n")
        c.print(_manual_instructions(clone, latest))
        return _FAILED

    c.print(f"  Checking out [bold]{latest}[/bold] …")
    checkout = _git(clone, "checkout", latest)
    if checkout.returncode != 0:
        c.print("  [red]git checkout failed:[/red]")
        c.print(f"  [dim]{(checkout.stderr or checkout.stdout).strip()}[/dim]\n")
        c.print(
            f"  The tag {latest} may not exist locally, or the checkout was blocked.\n"
            "  Resolve it, then re-run, or update manually:\n"
        )
        c.print(_manual_instructions(clone, latest))
        return _FAILED
    if checkout.stderr.strip():
        c.print(f"  [dim]{checkout.stderr.strip()}[/dim]")

    c.print("  Reinstalling the CLI …")
    install = subprocess.run(
        [uv, "tool", "install", str(clone), "--reinstall"],
        capture_output=True,
        text=True,
    )
    if install.returncode != 0:
        c.print("  [red]reinstall failed:[/red]")
        c.print(f"  [dim]{(install.stderr or install.stdout).strip()}[/dim]\n")
        c.print(_manual_instructions(clone, latest))
        return _FAILED

    c.print("\n  [green]✓ Updated.[/green] Run `canair --version` to confirm.\n")
    return _OK
