"""Offer to drop the source checkout's changes the contribution now holds."""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Any

from canair.contribute.workspace import backup_dir
from canair.core.prompt import confirm
from canair.render import glyphs
from canair.render.console import escape

from .state import Contribution


def tidy_source(run_: Contribution) -> dict[str, Any] | None:
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
    backup = backup_dir() / f"{datetime.now():%Y%m%d-%H%M%S}-source"
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
