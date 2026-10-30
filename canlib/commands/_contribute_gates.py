"""Pre-flight gates for ``canair contribute`` — what must hold before sharing.

The command's risk is concentrated *before* anything is pushed: a stale source
silently reverts upstream work, an invalid profile wastes a reviewer's time, a
capture note can leak a VIN, and running from inside the staging workspace copies
a profile onto itself. Those checks are policy, not plumbing, so they live here
rather than inline in the pipeline — one function per question, in the order the
pipeline asks them.

Every gate shares one contract: it either lets the run continue (returning
whatever it resolved) or reports the condition and raises
:class:`~canlib.commands._contribute_report.Stop`. Consent-based gates go through
:meth:`Reporter.gate`, so ``--json`` refuses without ``--yes`` instead of hanging
on a prompt no machine can answer.
"""

from __future__ import annotations

import argparse
import contextlib
import io
from pathlib import Path
from typing import Any

from .. import contribute as C
from .. import pii
from ..install_context import installed_snapshot_kind
from ._contribute_report import (
    CANNOT,
    Reporter,
    Stop,
    confirm,
    findings_json,
    replaced_json,
    rollback_json,
    unsplit_json,
)

# Warn before contributing a captures/ dir larger than this (bytes).
CAPTURES_WARN_BYTES = 25 * 1024 * 1024


def snapshot(rep: Reporter, profile) -> None:
    """Is the source a working checkout, or a frozen install snapshot?

    A bare ``canair`` resolves the profile from the ``site-packages`` copy, which
    can be behind the checkout (and ahead on captures written by bare ``--save``
    runs), so contributing it may revert upstream work.
    """
    kind = installed_snapshot_kind(profile.root)
    if not kind:
        return
    rep.gate(
        error=f"profile resolved from an installed {kind} snapshot, not a checkout; "
        "it may be stale",
        prompt="Contribute from the installed snapshot anyway?",
        human=lambda: rep.console.print(
            f"\n[yellow]⚠ This profile was read from an installed {kind} "
            f"snapshot[/yellow], not your working checkout:\n  [dim]{profile.root}[/dim]\n"
            "  That copy is frozen at install time — it can be behind your checkout\n"
            "  (and ahead on captures from bare `--save` runs), so contributing it may\n"
            "  revert upstream work. Prefer running [cyan]uv run canair contribute[/cyan] "
            "from your repo checkout.\n"
        ),
        installed_snapshot=kind,
    )


def validate(rep: Reporter, profile) -> None:
    """Refuse to contribute a profile that fails ``canair validate all``."""
    ok, report = _validate(profile)
    if ok:
        return

    def explain() -> None:
        rep.console.print("[red]Profile failed validation — fix it before contributing:[/red]\n")
        print(report)
        rep.console.print("  Run `canair validate all` for details.")

    rep.refuse("validation failed", human=explain)


def environment(rep: Reporter, profile, branch: str) -> C.Preflight:
    """Require the GitHub CLI, git, and an authenticated ``gh``."""
    pre = C.preflight()

    if not pre.gh:

        def install_gh() -> None:
            rep.console.print("\n[yellow]The GitHub CLI (`gh`) is not installed.[/yellow]\n")
            rep.console.print(C.gh_install_hint())
            rep.console.print("\n" + C.manual_instructions(profile.name, branch))

        rep.refuse("gh not found", human=install_gh)
    if not pre.git:
        rep.refuse(
            "git not found",
            human=lambda: rep.console.print("[yellow]`git` not found on PATH.[/yellow]"),
        )
    if not pre.authenticated:

        def sign_in() -> None:
            rep.console.print("\n[yellow]You're not signed in to GitHub.[/yellow] Run:\n")
            rep.console.print("    gh auth login\n")
            rep.console.print("  then re-run `canair contribute`.")

        rep.refuse("gh not authenticated", human=sign_in)

    return pre


def capture_size(rep: Reporter, profile, *, include_captures: bool) -> None:
    """Flag an oversized ``captures/`` before the clone — advisory, never fatal."""
    if not include_captures:
        return
    size = C.dir_size(profile.captures_dir)
    if size <= CAPTURES_WARN_BYTES:
        return
    mb = size / (1024 * 1024)
    if rep.json_mode:
        # A machine caller can't be prompted; surface it in the payload rather
        # than blocking a --diff/--json inspection of a big (but valid) profile.
        rep.warn(f"captures/ is large (~{mb:.0f} MB) — consider --no-captures")
        return
    rep.console.print(
        f"\n[yellow]captures/ is large (~{mb:.0f} MB).[/yellow] "
        "Consider `--no-captures` or trimming it first."
    )
    if not confirm("Include captures anyway?", rep.yes, json_mode=rep.json_mode):
        rep.console.print("  Aborted — nothing was contributed.")
        raise Stop(CANNOT)


def workspace(rep: Reporter, args, profile, pre: C.Preflight) -> Path:
    """Resolve the staging checkout, refusing to stage a profile into itself.

    The managed workspace is a full canair checkout, so it bundles profiles of its
    own; running canair from inside it resolves the active profile to the very
    directory the copy writes to. Even short of that, preparing the branch
    (:func:`canlib.contribute.start_branch`, then
    :func:`canlib.contribute.reset_workspace` for a managed one) rewrites the
    workspace's tracked files before the copy, so a source living there changes
    mid-run.
    """
    ws = Path(args.repo_dir).expanduser() if args.repo_dir else C.workspace_dir()
    rep.note(workspace=str(ws))

    collision = C.workspace_collision(profile.root, ws, profile.name)
    if collision is None:
        return _own_checkout_is_clean(rep, ws, profile, pre)

    def paths() -> None:
        rep.console.print(f"  profile:   [dim]{profile.root}[/dim]")
        rep.console.print(f"  workspace: [dim]{ws}[/dim]\n")

    if collision == "self":
        error = (
            "the profile being contributed IS the workspace's own copy — canair "
            "looks like it is running from inside its contribution workspace"
        )

        def explain() -> None:
            rep.console.print(f"\n[red]error:[/red] {error}:\n")
            paths()
            rep.console.print(
                "  Copying it onto itself can't contribute anything. Re-run "
                "[cyan]uv run canair contribute[/cyan]\n  from your own canair checkout "
                "(the workspace is a throwaway clone canair manages for you)."
            )

        # Unconditional: --yes cannot make a copy onto itself meaningful.
        rep.refuse(error, human=explain, workspace_collision=collision)

    warning = (
        "the profile being contributed lives inside the staging workspace; "
        "preparing the branch resets that checkout, so your source may change mid-run"
    )

    def warn_inside() -> None:
        rep.console.print(f"\n[yellow]⚠ {warning}:[/yellow]\n")
        paths()

    rep.gate(
        error=warning,
        prompt="Contribute from inside the workspace anyway?",
        human=warn_inside,
        workspace_collision=collision,
    )
    return ws


def _own_checkout_is_clean(rep: Reporter, ws: Path, profile, pre: C.Preflight) -> Path:
    """Warn when a user's own ``--repo-dir`` has uncommitted work in the profile dir.

    The managed workspace is rebuilt from the upstream base every run
    (:func:`canlib.contribute.reset_workspace`), so nothing there can leak into a
    PR. A ``--repo-dir`` checkout is never reset — it may hold hours of unrelated
    work — but ``commit_profile`` stages the whole profile directory, so any
    uncommitted edit already sitting in it *would* join the contribution.
    """
    if C.is_managed_workspace(ws):
        return ws
    dirty = C.local_changes(pre, ws, profile.name)
    if not dirty:
        return ws

    warning = (
        f"the checkout at {ws} has uncommitted changes under profiles/{profile.name}; "
        "they would be committed as part of this contribution"
    )

    def show() -> None:
        rep.console.print(f"\n[yellow]⚠ Uncommitted changes in {ws}:[/yellow]\n")
        for entry in dirty[:10]:
            rep.console.print(f"  [dim]{entry}[/dim]")
        if len(dirty) > 10:
            rep.console.print(f"  [dim]… and {len(dirty) - 10} more[/dim]")
        rep.console.print(
            f"\n  Everything under [cyan]profiles/{profile.name}/[/cyan] is staged, so "
            "these ride\n  along in the PR. canair leaves your own checkout alone — commit, "
            "stash or\n  revert them first, or drop [cyan]--repo-dir[/cyan] to use the "
            "managed workspace.\n"
        )

    rep.gate(
        error=warning,
        prompt="Contribute those changes too?",
        human=show,
        dirty_workspace=dirty,
    )
    return ws


def privacy(
    rep: Reporter,
    profile,
    pre: C.Preflight,
    ws: Path,
    *,
    include_captures: bool,
    rollback: list[tuple[str, int]],
) -> list[Any]:
    """The last consent gates before anything is committed: PII, then rollback.

    Returns the PII findings (empty when clean) so the caller can report them in
    the final payload.
    """
    # Scoped to what THIS contribution adds/changes vs upstream, so captures
    # already committed upstream are not re-flagged.
    findings = pii.scan_contribution(
        profile,
        ws / "profiles" / profile.name / "captures",
        include_captures=include_captures,
        base_reader=C.base_reader(pre, ws),
    )
    if findings:

        def list_findings() -> None:
            rep.console.print(
                f"\n[yellow]⚠ {len(findings)} possible privacy issue(s) in this "
                "contribution[/yellow] — the tree is public, so review before sharing:\n"
            )
            for f in findings:
                rep.console.print(f"  [yellow]•[/yellow] {f.location}  [dim]({f.detail})[/dim]")
            rep.console.print("")

        rep.gate(
            error="possible PII in the contribution",
            prompt="Contribute anyway?",
            human=list_findings,
            findings=findings_json(findings),
        )

    if rollback:
        rep.gate(
            error="this contribution removes committed upstream definition lines "
            "(source may be stale)",
            prompt="Contribute this rollback anyway?",
            human=lambda: rep.rollback_warning(rollback),
            rollback=rollback_json(rollback),
        )
    return findings


def captures(
    rep: Reporter,
    profile,
    pre: C.Preflight,
    ws: Path,
    *,
    include_captures: bool,
    replaced: list[C.CaptureReplacement],
) -> None:
    """The capture-integrity consent gates: replaced sessions, then unsplit replies.

    Both are judged on what *this* contribution changes. A replacement is how an
    edited session reaches upstream, but it is also exactly what a stale source
    reverting upstream's edit looks like — so each is listed and needs consent.
    An unsplit multi-DID reply (a pre-repair session) would re-enter upstream the
    bytes ``captures split-batched`` exists to correct; the detector is a corpus
    heuristic, so it warns and asks rather than refusing outright.
    """
    if not include_captures:
        return
    if replaced:
        rep.gate(
            error=f"this contribution replaces {len(replaced)} upstream capture session(s) "
            "with your copies (expected for sessions you edited; a stale source looks the same)",
            prompt="Replace those upstream sessions?",
            human=lambda: rep.replacement_warning(replaced),
            replaced_sessions=replaced_json(replaced),
        )
    unsplit = unsplit_in_contribution(profile, pre, ws)
    if unsplit:

        def explain() -> None:
            rep.console.print(
                f"\n[yellow]⚠ {len(unsplit)} capture(s) in this contribution hold a whole "
                "multi-DID reply stored under one DID[/yellow] — every analysis reads that "
                "DID past its real end:\n"
            )
            for file, location, pids in unsplit:
                rep.console.print(
                    f"  [yellow]•[/yellow] {file} {location}  [dim]({' + '.join(pids)})[/dim]"
                )
            rep.console.print(
                "\n  Repair them first with [cyan]canair captures split-batched[/cyan], "
                "then re-run.\n"
            )

        rep.gate(
            error=f"{len(unsplit)} unsplit multi-DID reply capture(s) in the contribution; "
            "repair them with `canair captures split-batched`",
            prompt="Contribute them unrepaired anyway?",
            human=explain,
            unsplit_batches=unsplit_json(unsplit),
        )


def unsplit_in_contribution(
    profile, pre: C.Preflight, ws: Path
) -> list[tuple[str, str, list[str]]]:
    """``(file, location, split_into)`` for each unsplit multi-DID reply the contribution adds.

    Detection runs over the prepared contribution's whole capture store (upstream
    plus overlay — the corpus the established DID lengths come from); findings in
    sessions byte-identical to the upstream base are not this contribution's doing
    and are left to the repair on upstream's side.
    """
    from canlib.captures import io as capture_io
    from canlib.captures.merge import session_key
    from canlib.captures.payload_corpus import load_payload_corpus
    from canlib.uds.unsplit_batches import find_unsplit_batches

    cdir = ws / "profiles" / profile.name / "captures"
    if not cdir.is_dir():
        return []
    corpus = load_payload_corpus(capture_io.iter_capture_files(cdir))
    found = find_unsplit_batches(corpus.samples)
    if not found:
        return []

    read_base = C.base_reader(pre, ws)
    upstream: dict[Path, set[str]] = {}
    sessions: dict[Path, list] = {}

    def is_upstream(path: Path, idx: int) -> bool:
        if path not in upstream:
            text = read_base(f"profiles/{profile.name}/captures/{path.name}")
            try:
                base = capture_io.parse_capture_text(text) if text else {}
            except ValueError:
                base = {}
            upstream[path] = {
                session_key(s)
                for s in (base or {}).get("sessions", []) or []
                if isinstance(s, dict)
            }
            sessions[path] = capture_io.load_capture_file(path).get("sessions", [])
        return session_key(sessions[path][idx]) in upstream[path]

    names = _rx_names(profile)
    out: list[tuple[str, str, list[str]]] = []
    for u in found:
        at = corpus.where[u.sample.handle]
        if is_upstream(at.path, at.session):
            continue
        ecu = names.get(u.sample.rx, hex(u.sample.rx))
        out.append((f"captures/{at.path.name}", at.describe(ecu, u.sample.pid), u.pids))
    return out


def _rx_names(profile) -> dict[int, str]:
    """RX → ECU name from the contributed profile's own registry (empty if unreadable)."""
    from ..ecus import build_rx_index, load_ecus

    try:
        return build_rx_index(load_ecus(profile.ecus_dir))
    except Exception:
        return {}


def _validate(profile) -> tuple[bool, str]:
    """Run ``validate all`` against ``profile``; return (ok, captured_output)."""
    from .validate import run as validate_run

    ns = argparse.Namespace(target="all", files=None, stats=False, strict=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = validate_run(ns)
    return rc == 0, buf.getvalue()
