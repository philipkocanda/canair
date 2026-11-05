"""The monitor's read-only overlays and its session-type picker.

The event log and the session info are read-only overlays: ``esc``, ``q`` and the key that
opened them close, and the arrows and ``g``/``G`` scroll. The session-type picker is the
shared filter-and-list picker over ECUs, then over the modes of the chosen ECU.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Label, Static
from textual.widgets.option_list import Option

from canair.core.eventlog import parse_event_line, read_event_log, run_id
from canair.render.glyphs import ARROW, EMPTY, SEP
from canair.tui import styles
from canair.tui.keys import (
    CLOSE_HINT,
    DISMISS_DESCRIPTION,
    PAGE_HINT,
    arrows_hint,
    bind,
    display_key,
    hint,
    key_hint,
    keys_for,
)
from canair.tui.modals import KitModal, ListPickerModal
from canair.tui.scroll import OVERLAY_SCROLL_BINDINGS, OverlayScroll
from canair.tui.styles import dialog_css

if TYPE_CHECKING:
    from canair.live.monitor.controller import MonitorController

# Central-log categories mapped to a display colour. Data-integrity faults (frames dropped
# or corrupted, replies in the wrong slot, bus errors, internal exceptions) are red, as the
# status bar shows them; the softer non-answers (no-data/decode) are yellow; the rest is dim. Categories are produced by canair.uds.response.classify_response +
# canair.core.eventlog.log_exception ("internal").
_EVENT_CATEGORY_STYLE = {
    "drop": styles.ERROR,
    "bus": styles.ERROR,
    "internal": f"bold {styles.ERROR}",
    "stale": styles.ERROR,
    "no_data": styles.WARNING,
    "decode": styles.WARNING,
}


class EventLogModal(OverlayScroll, KitModal[None]):
    """Scrollable overlay of this monitor session's diagnostics events.

    Snapshots the last N lines of ``canair logs`` (the same size-rotated file
    the transport-fault recorder writes drops/stale/timeouts/bus/decode and
    internal-exception events to) that this process wrote, and colour-codes them
    by category. Other sessions share the file, so their events are left out.
    Read-only; reopen to refresh. The full history and management live in
    ``canair logs``.
    """

    _MAX_LINES = 200

    SCROLL_ID: ClassVar[str] = "evlog-rows"

    CSS = (
        dialog_css("EventLogModal", "evlog", width=140)
        + "#evlog-box { width: 90%; height: 80%; }\n#evlog-rows { height: 1fr; }"
    )

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("back", "close", desc=DISMISS_DESCRIPTION),
        *bind("quit", "close", desc=DISMISS_DESCRIPTION),
        *bind("event_log", "close", desc=DISMISS_DESCRIPTION),
        *OVERLAY_SCROLL_BINDINGS,
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="evlog-box"):
            yield Label("Diagnostics events this session", id="evlog-title")
            with VerticalScroll(id="evlog-rows"):
                yield Static(self._render_events(), id="evlog-body", markup=False)
            yield Label(
                f"newest last {SEP} "
                + hint(arrows_hint("scroll"), PAGE_HINT, CLOSE_HINT)
                + f" {SEP} every session: canair logs",
                id="evlog-hint",
            )

    def on_mount(self) -> None:
        # Land on the newest events (the file is oldest→newest).
        self._scroller().scroll_end(animate=False)

    def _render_events(self) -> Text:
        lines = read_event_log(lines=self._MAX_LINES, run=run_id())
        if not lines:
            return Text("(no events logged this session)", style=styles.MUTED)
        out = Text()
        for line in lines:
            fields = parse_event_line(line)
            category = fields.get("category", "")
            style = _EVENT_CATEGORY_STYLE.get(category, styles.MUTED)
            out.append(line, style=style)
            out.append("\n")
        return out

    def action_close(self) -> None:
        self.dismiss(None)


def _keep_mode_label(keep_mode: str | None, keep_n: int | None) -> str:
    """Human-friendly description of a monitor keep/retain mode."""
    return {
        None: "none (latest only)",
        "all": "all (full time-series)",
        "changes": "changes (run-length dedup)",
        "last": f"last {keep_n}" if keep_n else "last N",
    }.get(keep_mode, str(keep_mode))


def _fmt_dt(dt) -> str:
    """Format a datetime as HH:MM:SS, or '?' when unavailable."""
    try:
        return dt.strftime("%H:%M:%S")
    except Exception:
        return "?"


def _fmt_elapsed(start) -> str:
    """Elapsed wall-clock since ``start`` as H:MM:SS (empty when unavailable)."""
    try:
        secs = int((datetime.now() - start).total_seconds())
    except Exception:
        return ""
    h, rem = divmod(max(secs, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


class SessionInfoModal(OverlayScroll, KitModal[None]):
    """Read-only overlay: the run's session summary + closed-segment history.

    Shows the current segment's label/states/notes, the run-level counters
    (frames, cycles, retain mode, transport, start time), where captures and the
    ``--save`` write-ahead journal are being written, and a list of the ``--save``
    segments already finished this run (the ``n`` rotations). It is a read-only
    snapshot: reopen to refresh, and use ``s`` in the monitor to relabel.
    """

    SCROLL_ID: ClassVar[str] = "sess-rows"

    CSS = (
        dialog_css("SessionInfoModal", "sess", width=120)
        + "#sess-box { width: 90%; height: 80%; }\n#sess-rows { height: 1fr; }"
    )

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("back", "close", desc=DISMISS_DESCRIPTION),
        *bind("quit", "close", desc=DISMISS_DESCRIPTION),
        *bind("info", "close", desc=DISMISS_DESCRIPTION),
        *OVERLAY_SCROLL_BINDINGS,
    ]

    def __init__(self, summary: dict, segments: list[dict]):
        super().__init__()
        self._summary = summary
        self._segments = segments

    def compose(self) -> ComposeResult:
        with Vertical(id="sess-box"):
            yield Label("Session info", id="sess-title")
            with VerticalScroll(id="sess-rows"):
                yield Static(self._build_body(), id="sess-body", markup=False)
            yield Label(hint(arrows_hint("scroll"), PAGE_HINT, CLOSE_HINT), id="sess-hint")

    def _build_body(self) -> Text:
        s = self._summary
        out = Text()

        def row(label: str, value: str) -> None:
            out.append(f"  {label:<16}", style=styles.HEADING)
            out.append(f"{value}\n")

        rec = "● yes (--save)" if s.get("recording") else "no (not recording)"
        states = s.get("states") or []
        states_txt = ", ".join(states) if states else "(auto-detected on save)"
        notes = (s.get("notes") or "").replace("\n", " ").strip() or EMPTY

        out.append("Current segment\n", style=styles.HEADING)
        row("label", s.get("label") or "Monitor")
        row("recording", rec)
        row("states", states_txt)
        row("notes", notes)
        row("query", s.get("query") or EMPTY)
        started = s.get("segment_started_at")
        seg_frames = s.get("segment_frames", 0)
        row("started", f"{_fmt_dt(started)}  ({seg_frames} frames)")

        out.append("\nRun summary\n", style=styles.HEADING)
        row("retain mode", _keep_mode_label(s.get("keep_mode"), s.get("keep_n")))
        row("view mode", str(s.get("view_mode", "full")))
        row("poll interval", f"{float(s.get('interval', 0.0)):.1f}s")
        if s.get("schedule"):
            row("poll schedule", str(s["schedule"]))
        row("cycles", str(s.get("cycle", 0)))
        row(
            "captured",
            f"{s.get('total_frames', 0)} frames {SEP} {s.get('unique_frames', 0)} unique",
        )
        if s.get("transport"):
            row("transport", str(s["transport"]))
        run_started = s.get("run_started_at")
        elapsed = _fmt_elapsed(run_started)
        run_txt = _fmt_dt(run_started) + (f"  (elapsed {elapsed})" if elapsed else "")
        row("run started", run_txt)
        if s.get("captures_dir"):
            row("captures dir", str(s["captures_dir"]))
        # Where the write-ahead journal is streaming to. Worth surfacing: it is
        # what `canair captures uds --recover` reads if this run is killed.
        if s.get("journal_path"):
            row("journal (WAL)", str(s["journal_path"]))
            out.append(
                "                  recoverable with: canair captures uds --recover\n",
                style=styles.MUTED,
            )

        n = len(self._segments)
        out.append(f"\nFinished segments this run ({n})\n", style=styles.HEADING)
        if not n:
            new_key = display_key(keys_for("new")[0])
            out.append(
                f"  (none yet {EMPTY} press {new_key} in the monitor to finish this session "
                "and start a new one)\n",
                style=styles.MUTED,
            )
        for i, seg in enumerate(self._segments, 1):
            label = seg.get("label") or "Monitor"
            span = f"{_fmt_dt(seg.get('started_at'))}{ARROW}{_fmt_dt(seg.get('ended_at'))}"
            frames = seg.get("frames", 0)
            seg_states = ", ".join(seg.get("states") or []) or EMPTY
            written = seg.get("written")
            out.append(f"  {i}. ", style=styles.HEADING)
            out.append(f"{label}\n")
            out.append(f"       {span} {SEP} {frames} frames {SEP} {seg_states}", styles.MUTED)
            if written:
                out.append(f" {ARROW} {written}", style=styles.MUTED)
            out.append("\n")
        return out

    def on_mount(self) -> None:
        self._scroller().scroll_home(animate=False)

    def action_close(self) -> None:
        self.dismiss(None)


class SessionPickerScreen(ListPickerModal["tuple[str, str]"]):
    """Diagnostic session-type picker: view/switch what's open per ECU.

    Two-step ``OptionList`` flow — pick an ECU (shown with the session it's
    currently tracked in, if any), then pick a mode. Modes come from the
    ECU's own known-good scan history (``MonitorSessions.known_types``,
    from ``canair scan sessions``) tagged known-good/known-unsupported, or the
    fixed safe fallback (01/03/81/82/83, tagged untested) when it has none.
    ``0x02``/``0x85`` (programming sessions) are never offered — canair.safety.blocklist
    blocks them outright. Dismisses with ``(ecu, mode)`` to switch, or
    ``None`` on cancel. The actual ``10 xx`` send is async, so it happens back
    in ``MonitorApp.action_session_type`` rather than in this modal.
    """

    CSS = ListPickerModal.picker_css("SessionPickerScreen", 74)

    BINDINGS: ClassVar[list[Binding]] = [*bind("back", "back")]

    def __init__(self, controller: MonitorController):
        super().__init__()
        self._controller = controller
        self._ecu: str | None = None

    def start(self) -> None:
        self._show_ecus()

    def options(self, needle: str) -> list[Option]:
        return self._ecu_options(needle) if self._ecu is None else self._mode_options(needle)

    def _ecu_options(self, needle: str) -> list[Option]:
        active = self._controller.sessions.active_modes()
        found: list[Option] = []
        for ecu in self._controller.selectors.available_ecus():
            if needle and needle not in ecu.lower():
                continue
            mode = active.get(ecu)
            suffix = f"  [dim](active: 0x10{mode})[/]" if mode else "  [dim](no session)[/]"
            found.append(Option(f"{ecu}{suffix}", id=ecu))
        return found

    def _mode_options(self, needle: str) -> list[Option]:
        assert self._ecu is not None
        active = self._controller.sessions.active_modes().get(self._ecu)
        found: list[Option] = []
        for mode, label, supported in self._controller.sessions.known_types(self._ecu):
            if supported is True:
                tag = "known-good"
            elif supported is False:
                tag = "known-unsupported"
            else:
                tag = "untested"
            if needle and needle not in f"{mode} {label} {tag}".lower():
                continue
            mark = f"{ARROW} " if mode == active else "  "
            desc = f" {label}" if label else ""
            found.append(Option(f"{mark}0x10{mode}{desc}  [dim]({tag})[/]", id=mode))
        return found

    def _show_ecus(self) -> None:
        self._ecu = None
        self.set_title(f"Session type {EMPTY} choose an ECU")
        self.set_hint(hint("accept", "filter", CLOSE_HINT))
        self.clear_filter()
        self.refill()

    def _show_modes(self, ecu: str) -> None:
        self._ecu = ecu
        active = self._controller.sessions.active_modes().get(ecu)
        title = f"Session type {EMPTY} {ecu}"
        if active:
            title += f"  [dim](current: 0x10{active})[/]"
        self.set_title(title)
        self.set_hint(hint("accept", "filter", key_hint("back")))
        self.clear_filter()
        self.refill()

    def chosen(self, option: Option) -> None:
        option_id = option.id or ""
        if self._ecu is None:
            self._show_modes(option_id)
            return
        self.dismiss((self._ecu, option_id))

    def action_back(self) -> None:
        if self._ecu is None:
            self.dismiss(None)
        else:
            self._show_ecus()
