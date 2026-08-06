"""``canair update`` — update the CLI from its git-clone install.

canair is installed from a git clone (``git clone`` + ``uv tool install .``), so
updating means: check out the latest **release tag** in the clone, then reinstall
the tool from it. This command locates that source clone (via uv's tool receipt,
falling back to the package's own repo root), reports the current vs latest
released version with a changelog link, and — after confirmation — runs
``git fetch --tags`` + ``git checkout <tag>`` + ``uv tool install <clone-dir> --reinstall``.

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
bare ``canair`` would run different code than ``uv run canair``). When there's no
newer release but the installed copy *is* out of sync, ``canair update`` offers a
reinstall-only resync (``uv tool install <clone> --reinstall``) — no network or
tag checkout needed — to bring the bare ``canair`` back in line with the clone.

The reported *current* version is the provenance-bearing one
(:func:`canlib.build_info.full_version`) — from a checkout it names the branch and
commit — while the release comparison runs on the pure package version.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .. import build_info
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


def _reinstall_instructions(clone: Path | None) -> str:
    """Manual steps for a reinstall-only resync (no tag checkout needed)."""
    loc = str(clone) if clone else "<your canair clone>"
    return f"  To reinstall manually:\n    uv tool install {loc} --reinstall\n"


def _confirm(c, yes: bool) -> bool:
    """Ask for confirmation (unless ``--yes``); True to proceed, False to abort.

    Prints the standard non-interactive / aborted notices as a side effect.
    """
    if yes:
        return True
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        c.print("  Non-interactive; pass --yes to proceed. Nothing changed.\n")
        return False
    try:
        answer = input("  Proceed? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""
    if answer not in ("y", "yes"):
        c.print("  Aborted. Nothing changed.\n")
        return False
    return True


def _do_reinstall(c, uv: str, clone: Path) -> int:
    """Run ``uv tool install <clone> --reinstall``; return an exit code."""
    c.print("  Reinstalling the CLI …")
    result = subprocess.run(
        [uv, "tool", "install", str(clone), "--reinstall"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        c.print("  [red]reinstall failed:[/red]")
        c.print(f"  [dim]{(result.stderr or result.stdout).strip()}[/dim]\n")
        return _FAILED
    return _OK


def _sync_reinstall(c, args, clone: Path | None, install: dict) -> int:
    """Resync the installed tool copy with the clone (no newer release).

    When the ``uv tool install`` copy's version has drifted from the source
    clone's ``pyproject.toml`` version but there's no newer release to check
    out, a plain ``uv tool install <clone> --reinstall`` brings the bare
    ``canair`` back in line with the clone (and with ``uv run canair``). No
    network or tag checkout is needed — the clone is already where it should be.
    """
    tool_version = install.get("tool_version")
    src_version = install.get("clone_version")
    c.print(
        f"  The installed `canair` ([bold]{tool_version}[/bold]) is out of sync "
        f"with the clone ([bold]{src_version}[/bold])."
    )
    if clone is None:
        c.print("  [yellow]Couldn't locate a git clone to reinstall from.[/yellow]")
        c.print(_reinstall_instructions(None))
        return _CANNOT

    uv = shutil.which("uv")
    if uv is None:
        c.print("  [yellow]`uv` not found on PATH — can't reinstall automatically.[/yellow]")
        c.print(_reinstall_instructions(clone))
        return _CANNOT

    c.print(f"  This will reinstall from [bold]{clone}[/bold] to sync them:")
    c.print(f"    uv tool install {clone} --reinstall\n")

    if not _confirm(c, args.yes):
        return _CANNOT

    rc = _do_reinstall(c, uv, clone)
    if rc != _OK:
        c.print(_reinstall_instructions(clone))
        return rc
    c.print("\n  [green]✓ Reinstalled.[/green] The installed `canair` now matches the clone.\n")
    return _OK


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
            "  [dim]run `canair update` (or `uv tool install <clone-dir> --reinstall`) to sync.[/dim]"
        )
    c.print("")


def run(args) -> int:
    from .. import __version__

    clone = _find_clone_dir()
    install = describe_install(clone)
    release = fetch_latest_release()
    latest = release["tag"] if release else None
    changelog = (release or {}).get("url") or CHANGELOG_URL

    clone_head = build_info.head_label(clone) if clone else None
    # Compare on the *pure* package version: a build-provenance local segment
    # (`+main.343b244`) says nothing about release ordering.
    update_available = _is_newer(latest, __version__)
    current = build_info.full_version()

    if args.json:
        import json

        print(
            json.dumps(
                {
                    "current": current,
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
    c.print(f"\n  [bold]canair[/bold]  current: {current}")
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

    if not update_available:
        # No newer release to check out. Either the installed tool copy has
        # drifted from the clone (offer a reinstall-only resync — no network,
        # no tag checkout), or we're genuinely up to date / can't determine a
        # tag (fall through to the offline refuse path below).
        if install["out_of_sync"]:
            return _sync_reinstall(c, args, clone, install)
        if latest is not None:
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

    if build_info.is_dirty(clone):
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
    c.print(
        f"    git fetch --tags  &&  git checkout {latest}  &&  uv tool install {clone} --reinstall\n"
    )

    if not _confirm(c, args.yes):
        return _CANNOT

    c.print("\n  Fetching tags …")
    fetch = build_info.run_git(clone, "fetch", "--tags", "--force")
    if fetch.returncode != 0:
        c.print("  [red]git fetch failed:[/red]")
        c.print(f"  [dim]{(fetch.stderr or fetch.stdout).strip()}[/dim]\n")
        c.print(_manual_instructions(clone, latest))
        return _FAILED

    c.print(f"  Checking out [bold]{latest}[/bold] …")
    checkout = build_info.run_git(clone, "checkout", latest)
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

    rc = _do_reinstall(c, uv, clone)
    if rc != _OK:
        c.print(_manual_instructions(clone, latest))
        return rc

    c.print("\n  [green]✓ Updated.[/green] Run `canair --version` to confirm.\n")
    return _OK
