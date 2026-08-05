"""Aggregate views over capture *metadata*: ``--summary`` and ``--sessions``.

Neither view decodes a payload — they answer "what is in the captures?" from the
session envelopes alone (counts per ECU/date, and the session table of contents
with each session's span, state, label, notes, transport and quality). The
payload-level views live in :mod:`listing` and :mod:`diff`.
"""

import re
import sys
from collections import defaultdict
from collections.abc import Sequence

from canlib.capture_types import CaptureEntry, Quality
from canlib.states import join_states as _join_states

from .query import _BOLD, _CYAN, _DIM, _RED, _RESET, _YELLOW, _dump_json, group_sessions


def cmd_summary(entries: Sequence[CaptureEntry], as_json: bool = False) -> None:
    """Print overview statistics."""
    by_ecu = defaultdict(int)
    by_date = defaultdict(int)
    payloads = 0
    scans = 0
    responses = 0

    for e in entries:
        by_ecu[e["ecu"]] += 1
        by_date[e["date"]] += 1
        if e["payload"]:
            payloads += 1
        elif e["scan_results"]:
            scans += 1
        elif e["response"]:
            responses += 1

    if as_json:
        _dump_json(
            {
                "files": len({e["file"] for e in entries}),
                "sessions": len({(e["file"], e["session_label"]) for e in entries}),
                "entries": len(entries),
                "payloads": payloads,
                "scans": scans,
                "responses": responses,
                "by_ecu": dict(sorted(by_ecu.items(), key=lambda x: -x[1])),
                "by_date": dict(sorted(by_date.items())),
            }
        )
        return

    print(f"\n  {_BOLD}Capture Summary{_RESET}")
    print(f"  Files:    {len({e['file'] for e in entries})}")
    print(f"  Sessions: {len({(e['file'], e['session_label']) for e in entries})}")
    print(f"  Entries:  {len(entries)} ({payloads} payloads, {scans} scans, {responses} responses)")

    print(f"\n  {_BOLD}By ECU:{_RESET}")
    for ecu, count in sorted(by_ecu.items(), key=lambda x: -x[1]):
        print(f"    {ecu:<12} {count:>4}")

    print(f"\n  {_BOLD}By Date:{_RESET}")
    for day, count in sorted(by_date.items()):
        print(f"    {day}  {count:>4}")
    print()


# ---------------------------------------------------------------------------
# Sessions mode (metadata table of contents)
# ---------------------------------------------------------------------------

_CTRL_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f]")


def _clean(text) -> str:
    """Sanitize a metadata string for terminal display (drop control sequences).

    Also collapses any whitespace run (incl. newlines from YAML block scalars)
    into single spaces so each field renders on one tidy line.
    """
    return " ".join(_CTRL_RE.sub("", str(text)).split())


def _quality_tag(quality: Quality | None) -> str:
    """A colored one-liner flagging a session's recorded drops/errors, or ''.

    Clean sessions (no drops/errors) return an empty string so the TOC stays
    uncluttered; only a session that recorded transport trouble gets a line —
    drops in red (the ISO-TP integrity signal), other errors in yellow, with the
    exchange total for context.
    """
    if not quality:
        return ""
    drops = (quality.get("drop", 0) or 0) + (quality.get("stale", 0) or 0)
    errs = sum(quality.get(k, 0) or 0 for k in ("no_data", "bus", "decode", "other"))
    parts: list[str] = []
    if drops:
        parts.append(f"{_RED}drops {drops}{_RESET}")
    if errs:
        parts.append(f"{_YELLOW}errors {errs}{_RESET}")
    if not parts:
        return ""
    ex = quality.get("exchanges")
    ex_txt = f" {_DIM}/ {ex} exchanges{_RESET}" if ex else ""
    return f"{_DIM}⚠ quality:{_RESET} " + " ".join(parts) + ex_txt


def cmd_sessions(
    entries: Sequence[CaptureEntry], as_json: bool = False, max_notes: int = 6
) -> None:
    """List capture *sessions* with their metadata — a searchable table of contents.

    Answers "what's in the captures?" without dumping payloads: one block per
    session showing date, time span, state, label, session notes, capture count,
    the ECUs touched, and distinct capture-level notes. Honors the shared scope
    filters (``--since``/``--until``/``--date``/``--state``/``--label``), so e.g.
    ``--sessions --state driving`` is a quick index of every drive.
    """
    sessions = group_sessions(entries)

    if as_json:
        import json

        out = [
            {
                "file": s["file"],
                "date": s["date"],
                "label": s["label"],
                "version": s["version"],
                "vehicle_states": s["vehicle_states"],
                "notes": s["notes"],
                "keep_mode": s["keep_mode"],
                "transport": s["transport"],
                "quality": s["quality"],
                "captures": s["n"],
                "ecus": list(s["ecus"]),
                "time_start": min(s["times"]) if s["times"] else None,
                "time_end": max(s["times"]) if s["times"] else None,
                "capture_notes": s["cap_notes"],
            }
            for s in sessions
        ]
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        return

    if not sessions:
        print("  No sessions found.")
        return

    print(f"\n  {_BOLD}Sessions{_RESET} — {len(sessions)} total\n")
    for s in sessions:
        span = ""
        if s["times"]:
            lo, hi = min(s["times"]), max(s["times"])
            lo, hi = lo.split(".")[0], hi.split(".")[0]
            span = lo if lo == hi else f"{lo}-{hi}"
        state_str = _join_states(s["vehicle_states"])
        state = f"  {_CYAN}{_clean(state_str)}{_RESET}" if state_str else ""
        print(f"  {_BOLD}{s['date']}{_RESET}{('  ' + _DIM + span + _RESET) if span else ''}{state}")
        if s["label"]:
            print(f"    {_clean(s['label'])}")
        if s["notes"]:
            print(f"    {_DIM}{_clean(s['notes'])}{_RESET}")
        ecus = ", ".join(s["ecus"]) or "—"
        keep = s.get("keep_mode")
        keep_tag = f" · {_CYAN}keep:{keep}{_RESET}{_DIM}" if keep else ""
        transport = s.get("transport")
        transport_tag = f" · {transport}" if transport else ""
        version = s.get("version")
        version_tag = f" · v{version}" if version else ""
        print(
            f"    {_DIM}{s['n']} captures · {ecus}{keep_tag}{transport_tag}{version_tag} · {s['file']}{_RESET}"
        )
        # Data-quality footprint: flag any drops/errors recorded for the session
        # (the transport-health provenance) so a suspect capture stands out.
        qtag = _quality_tag(s.get("quality"))
        if qtag:
            print(f"    {qtag}")
        # Distinct capture-level notes (RE annotations) — the other place notes live.
        for cn in s["cap_notes"][:max_notes]:
            clean = _clean(cn)
            trunc = clean if len(clean) <= 100 else clean[:97] + "..."
            print(f"      {_DIM}▸ {trunc}{_RESET}")
        if len(s["cap_notes"]) > max_notes:
            print(f"      {_DIM}… +{len(s['cap_notes']) - max_notes} more capture-notes{_RESET}")
        print()
