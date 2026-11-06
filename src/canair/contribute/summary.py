"""What a contribution adds, summarised for the pull request body.

``canair contribute`` stages the profile into a workspace clone; this module
compares the staged capture logs against the upstream base and describes only
what is new or replaced: which car (variant, ECU revisions), which ECUs and
vehicle states, how many sessions and captures, and how healthy the link was
(transport error counts, resyncs, disconnects). I/O-free apart from reading the
staged files, so it is testable without git or gh.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from canair.captures import io as capture_io
from canair.captures.io import capture_rx
from canair.captures.merge import session_anchor
from canair.ecus.registry import parse_ecu_ref

_ERROR_KEYS = ("drop", "stale", "no_data", "bus", "decode", "other")
_MAX_SESSION_ROWS = 30


@dataclass
class SessionLine:
    date: str
    label: str
    states: list[str]
    ecus: list[str]
    captures: int
    errors: int
    disconnects: int
    replaced: bool = False
    # Local wall-clock times of the first and last capture ("" when untimed).
    start: str = ""
    end: str = ""


@dataclass
class ContributionSummary:
    """The contribution's new and replaced sessions, aggregated."""

    definition_files: list[str] = field(default_factory=list)
    sessions: list[SessionLine] = field(default_factory=list)
    variants: Counter = field(default_factory=Counter)
    revisions: dict[str, set[str]] = field(default_factory=dict)
    ecus: Counter = field(default_factory=Counter)
    states: Counter = field(default_factory=Counter)
    transports: Counter = field(default_factory=Counter)
    versions: set[str] = field(default_factory=set)
    exchanges: int = 0
    errors: Counter = field(default_factory=Counter)
    resyncs: int = 0
    link_events: Counter = field(default_factory=Counter)

    @property
    def captures(self) -> int:
        return sum(s.captures for s in self.sessions)

    @property
    def added(self) -> int:
        return sum(not s.replaced for s in self.sessions)

    @property
    def replaced(self) -> int:
        return sum(s.replaced for s in self.sessions)

    @property
    def dates(self) -> list[str]:
        return sorted({s.date for s in self.sessions if s.date})

    def to_json(self) -> dict[str, Any]:
        return {
            "definition_files": self.definition_files,
            "sessions_added": self.added,
            "sessions_replaced": self.replaced,
            "captures": self.captures,
            "dates": self.dates,
            "variants": dict(self.variants),
            "revisions": {k: sorted(v) for k, v in self.revisions.items()},
            "ecus": dict(self.ecus),
            "states": dict(self.states),
            "transports": dict(self.transports),
            "versions": sorted(self.versions),
            "exchanges": self.exchanges,
            "errors": dict(self.errors),
            "resyncs": self.resyncs,
            "link_events": dict(self.link_events),
        }


def summarize(
    captures_dir: Path,
    *,
    profile_name: str,
    base_reader: Callable[[str], str | None],
    definition_files: Iterable[str] = (),
    rx_names: dict[int, str] | None = None,
) -> ContributionSummary:
    """Summarise the staged sessions under ``captures_dir`` that upstream lacks.

    ``base_reader(relpath)`` returns a file's upstream content (None when new);
    a session byte-identical to an upstream one is already shared and skipped.
    One sharing an upstream session's anchor is a replacement, counted as such.
    ``rx_names`` maps a response address to the ECU name shown.
    """

    out = ContributionSummary(definition_files=sorted(definition_files))
    if not captures_dir.is_dir():
        return out
    for path in capture_io.iter_capture_files(captures_dir):
        try:
            staged = capture_io.load_capture_file(path)
        except (OSError, ValueError):
            continue
        base_text = base_reader(f"profiles/{profile_name}/captures/{path.name}")
        base_sessions: list = []
        if base_text:
            try:
                base_sessions = capture_io.parse_capture_text(base_text).get("sessions") or []
            except ValueError:
                base_sessions = []
        base_keys = {_key(s) for s in base_sessions if isinstance(s, dict)}
        base_anchors = {session_anchor(s) for s in base_sessions if isinstance(s, dict)}
        for session in staged.get("sessions") or []:
            if not isinstance(session, dict) or _key(session) in base_keys:
                continue
            anchor = session_anchor(session)
            _add(
                out, session, rx_names or {}, replaced=anchor is not None and anchor in base_anchors
            )
    return out


def _key(session: Mapping[str, Any]) -> str:
    return json.dumps(session, sort_keys=True)


def _add(
    out: ContributionSummary,
    session: Mapping[str, Any],
    rx_names: dict[int, str],
    *,
    replaced: bool,
):
    captures = [c for c in session.get("captures") or [] if isinstance(c, dict)]
    ecus: set[str] = set()
    for cap in captures:
        rx = capture_rx(cap)
        ref = parse_ecu_ref(rx)
        if ref is None:
            continue  # "broadcast" discover rows name no single ECU
        ecus.add(rx_names.get(ref, str(rx)))
    states = [str(s) for s in session.get("vehicle_states") or []]
    raw_quality = session.get("quality")
    quality: dict = raw_quality if isinstance(raw_quality, dict) else {}
    errors = {k: int(quality.get(k) or 0) for k in _ERROR_KEYS if quality.get(k)}
    events = [e for e in session.get("link_events") or [] if isinstance(e, dict)]
    disconnects = sum(e.get("kind") == "disconnect" for e in events)
    # Canonical HH:MM:SS[.mmm] times sort as text, and a session never crosses
    # midnight (a recording spanning it is split into one session per day).
    times = [t for t in (c.get("time") for c in captures) if isinstance(t, str) and t]

    out.sessions.append(
        SessionLine(
            date=str(session.get("date") or ""),
            label=str(session.get("label") or ""),
            states=states,
            ecus=sorted(ecus),
            captures=len(captures),
            errors=sum(errors.values()),
            disconnects=disconnects,
            replaced=replaced,
            start=min(times, default=""),
            end=max(times, default=""),
        )
    )
    out.variants[str(session.get("variant") or "untagged")] += 1
    for ecu, rev in (session.get("revisions") or {}).items():
        out.revisions.setdefault(str(ecu), set()).add(str(rev))
    out.ecus.update(ecus)
    out.states.update(states)
    if session.get("transport"):
        out.transports[str(session["transport"])] += 1
    if session.get("version"):
        out.versions.add(str(session["version"]))
    out.exchanges += int(quality.get("exchanges") or 0)
    out.errors.update(errors)
    out.resyncs += int(quality.get("resyncs") or 0)
    out.link_events.update(str(e.get("kind")) for e in events if e.get("kind"))


def _counts(counter: Counter) -> str:
    return ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in counter.most_common()) or "—"


def render_markdown(summary: ContributionSummary, *, include_captures: bool) -> str:
    """The summary as Markdown for the pull request body."""
    lines: list[str] = []
    if summary.definition_files:
        lines.append(f"**Definitions changed ({len(summary.definition_files)}):**")
        lines += [f"- `{f}`" for f in summary.definition_files]
        lines.append("")
    if not include_captures:
        lines.append("Definitions only (no captures).")
        return "\n".join(lines)
    if not summary.sessions:
        lines.append("No new capture sessions.")
        return "\n".join(lines)

    s = summary
    dates = s.dates
    span = (dates[0] if len(dates) == 1 else f"{dates[0]} → {dates[-1]}") if dates else "—"
    sessions = f"{s.added} new"
    if s.replaced:
        sessions += f", {s.replaced} replaced"
    lines += [
        "### Captures",
        "",
        "| | |",
        "|---|---|",
        f"| Sessions | {sessions} ({s.captures} captures, {span}) |",
        f"| Variant | {_counts(s.variants)} |",
    ]
    if s.revisions:
        revs = ", ".join(f"{e}={'/'.join(sorted(r))}" for e, r in sorted(s.revisions.items()))
        lines.append(f"| ECU revisions | {revs} |")
    lines += [
        f"| ECUs ({len(s.ecus)}) | {', '.join(sorted(s.ecus)) or '—'} |",
        f"| States | {_counts(s.states)} |",
        f"| Transport | {_counts(s.transports)} |",
        f"| canair version | {', '.join(sorted(s.versions)) or '—'} |",
        "",
        "### Link quality",
        "",
        "| | |",
        "|---|---|",
        f"| Exchanges | {s.exchanges} |",
        f"| Transport errors | {_counts(s.errors) if s.errors else 'none'} |",
        f"| Resyncs | {s.resyncs} |",
        f"| Disconnects / reconnects | {s.link_events.get('disconnect', 0)} |",
    ]
    other = Counter({k: n for k, n in s.link_events.items() if k != "disconnect"})
    if other:
        lines.append(f"| Other link events | {_counts(other)} |")
    lines += [
        "",
        "<details><summary>Sessions</summary>",
        "",
        "| Date | Label | States | ECUs | Captures | Errors | Disconnects |",
        "|---|---|---|---|---|---|---|",
    ]
    for line in s.sessions[:_MAX_SESSION_ROWS]:
        label = line.label.replace("|", "\\|") + (" *(replaced)*" if line.replaced else "")
        lines.append(
            f"| {line.date} | {label} | {', '.join(line.states) or '—'} | {len(line.ecus)} | "
            f"{line.captures} | {line.errors} | {line.disconnects} |"
        )
    if len(s.sessions) > _MAX_SESSION_ROWS:
        lines.append(f"\n…and {len(s.sessions) - _MAX_SESSION_ROWS} more sessions.")
    lines += ["", "</details>"]
    return "\n".join(lines)
