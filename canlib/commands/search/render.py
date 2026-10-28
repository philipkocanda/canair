"""Output for ``canair search``: the table, the paste-ready command, and the JSON envelope."""

from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Sequence
from dataclasses import asdict

from canlib import ansi
from canlib.uds.notation import ByteDisplay, ByteNotation

from .findings import FIELD_PAYLOAD, Finding

NAME_PLACEHOLDER = "SIGNAL_NAME"


def upsert_command(finding: Finding, profile: str) -> str | None:
    """The ``pids upsert-param`` command that defines ``finding`` as an ascii signal.

    ``None`` when no single expression is correct for the finding. The signal name is
    left as a placeholder (a bare ``SIGNAL_NAME``) for the user to choose; the profile
    is explicit because a write without it lands in whatever the default resolves to.
    """
    if finding.expression is None:
        return None
    parts = [
        "canair",
        "--profile",
        shlex.quote(profile),
        "pids",
        "upsert-param",
        shlex.quote(finding.ecu),
        shlex.quote(finding.pid),
        NAME_PLACEHOLDER,
        shlex.quote(finding.expression),
        "--type",
        "ascii",
    ]
    return " ".join(parts)


def _at(finding: Finding, notation: ByteNotation) -> str:
    """Where the finding sits: a byte label for a payload hit, else the field it was in.

    A WiCAN index depends on the frame layout, so for a group that mixes single- and
    multi-frame captures only the ISO-TP index names the byte correctly in all of them.
    """
    if finding.offset is None:
        return finding.field
    last = finding.offset + finding.length - 1
    span = f"i{finding.offset}" if finding.length <= 1 else f"i{finding.offset}-{last}"
    if finding.mixed_frames and notation is ByteNotation.WICAN:
        return f"{span} (mixed SF/MF)"
    display = ByteDisplay(notation, finding.payload_len)
    label = display.ref(finding.offset)
    if notation is not ByteNotation.ISOTP:
        label = f"{label} ({span})"
    return label


def _seen(finding: Finding) -> str:
    if finding.first and finding.last and finding.first != finding.last:
        return f"{finding.first}..{finding.last}"
    return finding.first or finding.last


def _highlight(text: str, match: str | None) -> str:
    """``text`` quoted, with the first occurrence of ``match`` in bold.

    A free-text snippet has its whitespace collapsed, so the match is collapsed the
    same way before it is looked for.
    """
    if match:
        needle = " ".join(match.split()) or match
        at = text.lower().find(needle.lower())
        if at != -1:
            end = at + len(needle)
            return f"'{text[:at]}{ansi.BOLD}{text[at:end]}{ansi.RESET}{text[end:]}'"
    return f"'{text}'"


def _notes(finding: Finding) -> str:
    extra = []
    if finding.variants > 1:
        extra.append(f"+{finding.variants - 1} variant{'s' if finding.variants > 2 else ''}")
    if finding.source == "payload" and finding.field != FIELD_PAYLOAD:
        extra.append(f"from {finding.field}")
    if finding.lossy:
        extra.append("lossy: raw hex truncated upstream")
    return f"  {ansi.DIM}({', '.join(extra)}){ansi.RESET}" if extra else ""


def print_table(findings: Sequence[Finding], *, notation: ByteNotation, profile: str) -> None:
    """Print one aligned row per finding, with its paste-ready command beneath."""
    cells = [
        (f.ecu, f.pid, _at(f, notation), str(f.length) if f.offset is not None else "", f.hits)
        for f in findings
    ]
    widths = [
        max(len(h), *(len(str(c[i])) for c in cells))
        for i, h in enumerate(("ECU", "PID", "AT", "LEN", "HITS"))
    ]
    header = ("ECU", "PID", "AT", "LEN", "HITS")
    line = "  ".join(
        h.rjust(w) if h in ("LEN", "HITS") else h.ljust(w)
        for h, w in zip(header, widths, strict=True)
    )
    print(f"  {ansi.DIM}{line}  SEEN / TEXT{ansi.RESET}")
    for f, cell in zip(findings, cells, strict=True):
        row = "  ".join(
            str(v).rjust(w) if i >= 3 else str(v).ljust(w)
            for i, (v, w) in enumerate(zip(cell, widths, strict=True))
        )
        seen = _seen(f)
        print(f"  {row}  {ansi.DIM}{seen}{ansi.RESET}  {_highlight(f.text, f.match)}{_notes(f)}")
        command = upsert_command(f, profile)
        if command:
            print(f"  {ansi.DIM}  → {command}{ansi.RESET}")


def print_truncation(shown: int, total: int) -> None:
    """The loud, always-printed footer: a cap must never hide results silently."""
    print(
        f"\n  {ansi.YELLOW}… {total - shown} more not shown{ansi.RESET} "
        f"(showing {shown} of {total}). Use {ansi.BOLD}--limit 0{ansi.RESET} for all, "
        f"or narrow with --query/--since/--last-session/--state."
    )


def finding_dict(finding: Finding, profile: str) -> dict:
    data = asdict(finding)
    data["command"] = upsert_command(finding, profile)
    return data


def dump_json(envelope: dict) -> None:
    json.dump(envelope, sys.stdout, indent=2, default=str)
    print()
