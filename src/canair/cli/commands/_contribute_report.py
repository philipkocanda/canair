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
  :mod:`canair.cli.commands.contribute` readable as a sequence of steps.

The outcome kinds map to the exit codes: :data:`OK` (done), :data:`FAILED` (an operation
went wrong — git/gh/network), :data:`DECLINED` (the user answered no) and :data:`CANNOT` (the
contribution is impossible, or a prompt had to be answered with no terminal to answer it).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from canair.core.prompt import confirm
from canair.render import glyphs
from canair.render.console import escape

if TYPE_CHECKING:
    from rich.console import Console

OK = 0
FAILED = 1
DECLINED = 1
CANNOT = 2


class Stop(Exception):
    """Terminate the pipeline with ``code``; the outcome is already reported."""

    def __init__(self, code: int) -> None:
        super().__init__(f"contribution stopped with exit code {code}")
        self.code = code


def rollback_json(rollback: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """The ``rollback`` payload shape: which files lose how many upstream lines."""
    return [{"path": path, "removed_lines": n} for path, n in rollback]


def replaced_json(replaced: list[Any]) -> list[dict[str, Any]]:
    """The ``replaced_sessions`` payload shape (:class:`canair.contribute.CaptureReplacement`)."""
    return [
        {
            "file": r.file,
            "session": r.session.index,
            "date": r.session.date,
            "time": r.session.time,
            "label": r.session.label,
            "fields": list(r.session.fields),
        }
        for r in replaced
    ]


def unsplit_json(unsplit: list[Any]) -> list[dict[str, Any]]:
    """The ``unsplit_batches`` payload shape: ``(file, location, pids)`` per finding."""
    return [{"file": f, "location": loc, "split_into": list(pids)} for f, loc, pids in unsplit]


def findings_json(findings: list[Any]) -> list[dict[str, str]]:
    """The ``findings`` payload shape (see :mod:`canair.contribute.pii`)."""
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
            self.console.print(f"  [yellow]{glyphs.WARN}[/yellow] {escape(message)}")

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
        self.confirm(prompt, error=error)

    def confirm(self, prompt: str, *, error: str, after_decline: str = "") -> None:
        """Return when the user agrees; end the run (exit 1) when they decline.

        ``--yes`` agrees ahead of time. ``--json`` never does: it cannot be asked, so it
        refuses and names ``error``. With no terminal the shared prompt raises
        ``PromptUnavailable``, which the top level reports (exit 2).
        """
        if self.yes:
            return
        if self.json_mode:
            self.refuse(f"{error}; re-run with --yes to proceed")
        if not confirm(prompt, yes=False):
            if after_decline:
                self.console.print(f"  {after_decline}")
            raise Stop(DECLINED)

    def rollback_warning(self, rollback: list[tuple[str, int]]) -> None:
        """Explain that the contribution removes committed upstream definition lines.

        Shown by both ``--diff`` (informationally) and the rollback gate (before
        asking), hence a method rather than inline prose.
        """
        self.console.print(
            f"\n[yellow]{glyphs.WARN} This contribution removes committed upstream definition "
            "lines[/yellow] — curated definitions normally only grow, so if your source\n"
            "  is stale this would revert work already merged upstream:\n"
        )
        for path, removed in rollback:
            self.console.print(
                f"  [yellow]•[/yellow] {escape(path)}  [dim](−{removed} line(s))[/dim]"
            )
        self.console.print(
            "\n  If this is a deliberate cleanup, proceed. Otherwise sync your source "
            "first\n  (e.g. [cyan]git pull[/cyan], and run [cyan]uv run canair[/cyan] "
            "from your checkout).\n"
        )

    def replacement_warning(self, replaced: list[Any]) -> None:
        """Explain that the contribution replaces upstream capture sessions in place.

        An edited session (``--set-notes``, ``migrate-discover``, ``split-batched`` …)
        is the same recording as upstream's copy, so it replaces that copy rather
        than sitting next to it. A *stale* source looks identical — upstream edited
        the session later — so every replacement is listed for review. Shown by
        ``--diff`` and by the replacement gate, like :meth:`rollback_warning`.
        """
        self.console.print(
            f"\n[yellow]{glyphs.WARN} This contribution replaces {len(replaced)} upstream capture "
            "session(s)[/yellow] with your copies of the same recordings:\n"
        )
        for r in replaced:
            s = r.session
            when = f"{s.date} {s.time}".strip()
            changed = ", ".join(s.fields) or "content"
            self.console.print(
                f"  [yellow]•[/yellow] {escape(r.file)} \\[{s.index}] {escape(when)}  "
                f"{escape(s.label)}  [dim](changes: {escape(changed)})[/dim]"
            )
        self.console.print(
            "\n  That is expected for sessions you edited or repaired. If you did not, "
            "your\n  source is probably behind upstream and this would revert upstream's "
            "edit —\n  sync first (e.g. [cyan]git pull[/cyan]).\n"
        )

    def show_source_changes(self, changes: Any, profile_name: str, backup: Path) -> None:
        """List the source checkout's uncommitted changes that the contribution already holds."""
        console = self.console
        console.print(
            f"\n  Your checkout [dim]{escape(str(changes.checkout))}[/dim] still holds "
            f"{len(changes.held)} uncommitted change(s)\n  under "
            f"[cyan]profiles/{escape(profile_name)}/[/cyan] that "
            "the contribution already holds:\n"
        )
        for path in changes.held[:10]:
            console.print(f"    [dim]{escape(str(path))}[/dim]")
        if len(changes.held) > 10:
            console.print(f"    [dim]… and {len(changes.held) - 10} more[/dim]")
        if changes.not_held:
            console.print(
                f"\n  {len(changes.not_held)} other change(s) there are not in the PR and are "
                "left alone."
            )
        console.print(
            "\n  Pulling the merged PR would conflict with them. Discarding restores them to\n"
            f"  your last commit, after a backup to [cyan]{escape(str(backup))}[/cyan].\n"
        )
