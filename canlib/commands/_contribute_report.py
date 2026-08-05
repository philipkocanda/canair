"""Dual-mode (human / ``--json``) reporting for ``canair contribute``.

``contribute`` is a long linear pipeline of pre-flight gates — is the profile a
stale snapshot? does it validate? is ``gh`` installed? is this being run from
inside the staging workspace? does the diff look like a rollback? is there PII in
it? — and **every one of them has to report the same condition twice**: a rich
explanation for a terminal, and a machine-readable payload under ``--json``.

Written inline that duality repeats per gate, and drifts: payloads grew
different identity fields, the "re-run with --yes" wording diverged, and the
happy path threaded an exit code through a dozen ``if`` arms. This module owns
it instead:

* :class:`Reporter` renders one outcome both ways and accumulates the identity
  fields (profile, workspace, branch, …) so every payload carries the same
  context.
* Gates signal "stop here" by raising :class:`Stop` rather than returning a code
  the caller must notice and forward, which is what keeps the pipeline in
  :mod:`canlib.commands.contribute` readable as a sequence of steps.

The three outcome kinds map to the exit codes: :data:`OK` (done),
:data:`FAILED` (an operation went wrong — git/gh/network) and :data:`CANNOT`
(the contribution is impossible or was declined).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:
    from rich.console import Console

OK = 0
FAILED = 1
CANNOT = 2


class Stop(Exception):
    """Terminate the pipeline with ``code``; the outcome is already reported."""

    def __init__(self, code: int) -> None:
        super().__init__(f"contribution stopped with exit code {code}")
        self.code = code


def confirm(prompt: str, yes: bool, *, json_mode: bool) -> bool:
    """Ask to proceed unless ``yes``; non-interactive without ``yes`` declines."""
    if yes:
        return True
    if json_mode or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def rollback_json(rollback: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """The ``rollback`` payload shape: which files lose how many upstream lines."""
    return [{"path": path, "removed_lines": n} for path, n in rollback]


def findings_json(findings: list[Any]) -> list[dict[str, str]]:
    """The ``findings`` payload shape (see :mod:`canlib.pii`)."""
    return [{"location": f.location, "kind": f.kind, "detail": f.detail} for f in findings]


@dataclass
class Reporter:
    """Reports one outcome as human output or a ``--json`` payload.

    ``base`` accumulates the identity fields as the pipeline learns them (profile
    → workspace → branch/mode), so a gate only supplies what is specific to *it*.
    ``warnings`` collects non-fatal advisories (a skipped capture file, an
    oversized ``captures/``) that ride along in every payload.
    """

    console: Console
    json_mode: bool
    yes: bool
    base: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def note(self, **fields: Any) -> None:
        """Record identity fields to include in every subsequent payload."""
        self.base.update({k: v for k, v in fields.items() if v is not None})

    def warn(self, message: str) -> None:
        """Record a non-fatal advisory (printed now; reported in every payload)."""
        self.warnings.append(message)
        if not self.json_mode:
            self.console.print(f"  [yellow]⚠[/yellow] {message}")

    def payload(self, **extra: Any) -> dict[str, Any]:
        return {**self.base, **extra, "warnings": list(self.warnings)}

    def emit(self, payload: dict[str, Any]) -> int:
        print(json.dumps(payload, indent=2))
        return OK if payload.get("ok") else (CANNOT if payload.get("cannot") else FAILED)

    def done(self, human: Callable[[], None], **extra: Any) -> int:
        """Report a successful outcome (a PR, a diff, a dry run, or a no-op)."""
        if self.json_mode:
            return self.emit(self.payload(ok=True, **extra))
        human()
        return OK

    def refuse(
        self, error: str, *, human: Callable[[], None] | None = None, **extra: Any
    ) -> NoReturn:
        """The contribution cannot proceed (broken, impossible, or declined)."""
        if self.json_mode:
            self.emit(self.payload(ok=False, cannot=True, error=error, **extra))
        elif human is not None:
            human()
        raise Stop(CANNOT)

    def fail(
        self, error: str, *, detail: str = "", human: Callable[[], None] | None = None
    ) -> NoReturn:
        """An operation went wrong (git/gh/network) — not the user's doing."""
        if self.json_mode:
            self.emit(self.payload(ok=False, error=error, detail=detail))
        elif human is not None:
            human()
        raise Stop(FAILED)

    def gate(self, *, error: str, prompt: str, human: Callable[[], None], **extra: Any) -> None:
        """A risky-but-allowed condition: warn, then require consent to continue.

        ``--json`` cannot ask, so it refuses without ``--yes`` (reporting the
        condition) and proceeds silently with it. Interactively the user sees
        ``human()`` and answers ``prompt``.
        """
        if self.json_mode:
            if not self.yes:
                self.refuse(f"{error}; re-run with --yes to proceed", **extra)
            return
        human()
        if not confirm(prompt, self.yes, json_mode=self.json_mode):
            self.console.print("  Aborted — nothing was contributed.")
            raise Stop(CANNOT)

    def rollback_warning(self, rollback: list[tuple[str, int]]) -> None:
        """Explain that the contribution removes committed upstream definition lines.

        Shown by both ``--diff`` (informationally) and the rollback gate (before
        asking), hence a method rather than inline prose.
        """
        self.console.print(
            "\n[yellow]⚠ This contribution removes committed upstream definition "
            "lines[/yellow] — curated definitions normally only grow, so if your source\n"
            "  is stale this would revert work already merged upstream:\n"
        )
        for path, removed in rollback:
            self.console.print(f"  [yellow]•[/yellow] {path}  [dim](−{removed} line(s))[/dim]")
        self.console.print(
            "\n  If this is a deliberate cleanup, proceed. Otherwise sync your source "
            "first\n  (e.g. [cyan]git pull[/cyan], and run [cyan]uv run canair[/cyan] "
            "from your checkout).\n"
        )
