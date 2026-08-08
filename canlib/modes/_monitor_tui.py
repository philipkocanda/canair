"""Textual TUI for the live monitor (``canair monitor``).

The latest values render into a single widget that updates *in place* inside a
scrollable container. The scroll position is **never** moved by a data refresh:
values keep updating wherever you are, so a byte you are reading can't be yanked
out from under you. ``space`` pauses polling; ``G``/``End`` jumps to the newest
output when you do want the tail.

The monitor doubles as a lightweight PID editor: ``↑``/``↓`` move a selection
cursor over the decoded signal rows, ``e`` opens an in-place edit dialog
(expression / unit / min / max / notes / verified / enabled), ``v`` and ``d``
quick-toggle the selected signal's verified/enabled flags, and ``F`` cycles
a display filter (all / verified / unverified / enabled / disabled). Edits are
written through :mod:`canlib.pids_edit` and picked up on the next poll.

With ``--save`` a blinking ``● REC`` marks the active recording. Because every
payload is journaled and written to a capture file automatically on exit, ``s``
only *labels* the current recording (label / state / notes) — it doesn't write a
separate file. ``n`` **finishes** the current session (writing it to a capture
file now) and starts a fresh, separately-labelled one, so a single run can yield
several sessions. Without ``--save``, ``s`` performs a one-off write of the
payloads captured so far and ``n`` is unavailable.

``V`` cycles the display view mode (``ecus`` → ``ranges`` → ``signals`` →
``full``): a bare ECU list, each signal's captured value span, the decoded
signals only, or the signals plus raw byte payloads (the default). ``i`` opens a
read-only session-info overlay showing the current segment's label/state/notes,
the run's frame/cycle counters and retain mode, where the ``--save`` journal is
being written, and the history of finished ``--save`` segments this run.

The bottom bar is built from :mod:`canlib.tui_status`, so it fits itself to the
terminal width instead of clipping its tail on a narrow screen; ``?`` remains
the authoritative key list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from canlib.tui_help import HelpMixin
from canlib.tui_scroll import reveal_marker
from canlib.tui_status import P_ESSENTIAL, P_HIGH, P_LOW, P_NORMAL, StatusBar, StatusItem

if TYPE_CHECKING:
    from .monitor import MonitorController

import asyncio
import time
from datetime import datetime

from ..log import log_event, log_exception
from ..transport.errors import transport_error_types


def _split_state_tokens(value: str) -> tuple[list[str], str]:
    """Split a comma-separated state string into (completed tokens, active token).

    The *active token* is the trailing fragment after the last comma — the one
    being typed and thus the one autocomplete filters against. Completed tokens
    are everything before it, stripped of surrounding whitespace. A trailing
    comma yields an empty active token (nothing being typed yet).
    """
    parts = value.split(",")
    active = parts[-1].strip()
    completed = [p.strip() for p in parts[:-1] if p.strip()]
    return completed, active


def _complete_state_token(value: str, choice: str) -> str:
    """Return ``value`` with its active (last) token replaced by ``choice``.

    Preserves the already-typed tokens and appends a ``", "`` so the caret is
    ready for the next state — e.g. ``("ready, pa", "parked") -> "ready, parked, "``.
    """
    completed, _active = _split_state_tokens(value)
    return ", ".join([*completed, choice]) + ", "


def _unknown_state_tokens(value: str, vocabulary: set[str]) -> list[str]:
    """Tokens in ``value`` (comma-separated) that aren't in ``vocabulary``.

    Case-insensitive; the trailing token still being typed is ignored while it
    remains a prefix of some known state (so the warning doesn't flicker mid-word).
    """
    completed, active = _split_state_tokens(value)
    unknown = [t for t in completed if t.lower() not in vocabulary]
    if active and active.lower() not in vocabulary:
        if not any(name.startswith(active.lower()) for name in vocabulary):
            unknown.append(active)
    return unknown


class SaveDialog(ModalScreen[tuple[str, str, str] | None]):
    """Modal prompt for capture metadata (label / state / notes).

    The state field is a free-text ``Input`` (states are comma-separated and may
    be composite, e.g. ``ready, parked``) augmented with a filtering autocomplete
    dropdown of the profile's known states and a live warning for any token
    outside the vocabulary. Dismisses with ``(label, state, notes)`` on save, or
    ``None`` on cancel.
    """

    CSS = """
    SaveDialog { align: center middle; background: $background 60%; }
    #dialog {
        width: 84; max-width: 90%; height: auto; max-height: 90%; padding: 1 3;
        border: round $accent; background: $surface;
    }
    #dialog-title { text-style: bold; margin-bottom: 1; }
    #dialog-caption { color: $text-muted; margin-bottom: 1; height: auto; }
    #dialog Input { margin-bottom: 0; }
    #state-hint { color: $text-muted; height: 1; margin-bottom: 0; }
    #state-status { color: $text-muted; height: auto; margin-bottom: 0; }
    #state-warning { color: $warning; height: auto; margin-bottom: 1; display: none; }
    #state-warning.visible { display: block; }
    #state-options {
        height: auto; max-height: 8; margin-bottom: 1;
        border: round $panel; background: $panel; display: none;
    }
    #state-options.visible { display: block; }
    #dialog-buttons { height: auto; align-horizontal: right; margin-top: 1; }
    #dialog-buttons Button { margin-left: 2; }
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "cancel")]

    def __init__(
        self,
        suggested_label: str,
        suggested_state: str = "",
        state_options: list[tuple[str, str]] | None = None,
        title: str = "Save captures to profile",
        caption: str = "",
        save_button: str = "Save",
    ):
        super().__init__()
        self._suggested = suggested_label
        self._suggested_state = suggested_state
        self._title = title
        self._caption = caption
        self._save_button = save_button
        if state_options is None:
            from ..states import state_options as load_state_options

            try:
                state_options = load_state_options()
            except Exception:
                state_options = []
        self._state_options = state_options
        self._vocabulary = {name for name, _desc in state_options}

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical(id="dialog"):
            yield Label(self._title, id="dialog-title")
            if self._caption:
                yield Static(self._caption, id="dialog-caption", markup=True)
            yield Input(value=self._suggested, placeholder="Label (required)", id="f-label")
            yield Input(
                value=self._suggested_state,
                placeholder="States (comma-separated, e.g. ready, parked)",
                id="f-state",
            )
            yield Label("↑/↓ + enter to ADD a state to the field · type to filter", id="state-hint")
            yield OptionList(id="state-options")
            yield Label("", id="state-status")
            yield Label("", id="state-warning")
            yield Input(placeholder="Notes (optional)", id="f-notes")
            with Horizontal(id="dialog-buttons"):
                yield Button(self._save_button, variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#f-label", Input).focus()
        self._refresh_state_ui(self._suggested_state)

    def _option_for(self, name: str, desc: str) -> Option:
        prompt = name if not desc else f"{name} — {desc}"
        return Option(prompt, id=name)

    def _refresh_state_ui(self, value: str) -> None:
        """Repopulate the dropdown for the active token and update the warning."""
        _completed, active = _split_state_tokens(value)
        needle = active.lower()
        matches = [
            (name, desc)
            for name, desc in self._state_options
            if needle in name or needle in desc.lower()
        ]

        options = self.query_one("#state-options", OptionList)
        options.clear_options()
        for name, desc in matches:
            options.add_option(self._option_for(name, desc))
        options.set_class(bool(matches), "visible")

        warning = self.query_one("#state-warning", Label)
        unknown = _unknown_state_tokens(value, self._vocabulary)
        if unknown:
            warning.update("unknown state(s): " + ", ".join(unknown))
            warning.set_class(True, "visible")
        else:
            warning.update("")
            warning.set_class(False, "visible")

        # The state field is free-text, not a checkbox — nothing is ever
        # "selected". Make that explicit: when empty, say the state will be
        # auto-detected from data on save (the span-aware back-fill); when it
        # still holds the auto-suggested value, mark it as auto-detected so the
        # user knows it was pre-filled (and can edit/clear it).
        status = self.query_one("#state-status", Label)
        stripped = value.strip().rstrip(",").strip()
        if not stripped:
            status.update("no state set — will auto-detect from data on save")
        elif self._suggested_state and stripped == self._suggested_state.strip():
            status.update(f"state auto-detected: {stripped} (edit or clear)")
        else:
            status.update("")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "f-state":
            self._refresh_state_ui(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        state_input = self.query_one("#f-state", Input)
        state_input.value = _complete_state_token(state_input.value, event.option.id or "")
        state_input.cursor_position = len(state_input.value)
        state_input.focus()
        self._refresh_state_ui(state_input.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # In the state field, enter first drops focus into the dropdown so a
        # match can be picked; only submit from the other fields.
        if event.input.id == "f-state":
            options = self.query_one("#state-options", OptionList)
            if "visible" in options.classes and options.option_count:
                options.focus()
                if options.highlighted is None:
                    options.highlighted = 0
                return
        self._submit()

    def _submit(self) -> None:
        label = self.query_one("#f-label", Input).value.strip() or self._suggested
        state = self.query_one("#f-state", Input).value.strip().rstrip(",").strip()
        notes = self.query_one("#f-notes", Input).value.strip()
        self.dismiss((label, state, notes))

    def action_cancel(self) -> None:
        self.dismiss(None)


class EditParamDialog(ModalScreen[dict | None]):
    """Modal editor for a single PID parameter's definition.

    Prefilled from the selected parameter's current fields; dismisses with a
    ``{expression, unit, min, max, notes, verified, enabled}`` dict on save, or
    ``None`` on cancel. Writing is done by the caller (via the monitor editor).
    """

    CSS = """
    EditParamDialog { align: center middle; background: $background 60%; }
    #edit-dialog {
        width: 90%; max-width: 100; height: auto; max-height: 90%; padding: 1 2;
        border: round $accent; background: $surface;
    }
    #edit-title { text-style: bold; margin-bottom: 1; }
    #edit-scroll { height: auto; max-height: 1fr; }
    #edit-scroll .edit-label { color: $text-muted; margin-top: 1; }
    #edit-dialog Input { margin-bottom: 0; }
    #edit-dialog Checkbox { height: 1; margin-top: 1; }
    #edit-buttons { height: auto; align-horizontal: right; margin-top: 1; }
    #edit-buttons Button { margin-left: 2; }
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, target: dict):
        super().__init__()
        self._target = target

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        t = self._target
        title = f"Edit {t.get('ecu', '')} {t.get('pid', '')} {t.get('name', '')}"
        with Vertical(id="edit-dialog"):
            yield Label(title.strip(), id="edit-title")
            with VerticalScroll(id="edit-scroll"):
                yield Label("expression", classes="edit-label")
                yield Input(value=t.get("expression", ""), placeholder="expression", id="e-expr")
                yield Label("unit", classes="edit-label")
                yield Input(value=t.get("unit", ""), placeholder="unit", id="e-unit")
                yield Label("min", classes="edit-label")
                yield Input(value=t.get("min", ""), placeholder="min", id="e-min")
                yield Label("max", classes="edit-label")
                yield Input(value=t.get("max", ""), placeholder="max", id="e-max")
                yield Label("notes", classes="edit-label")
                yield Input(value=t.get("notes", ""), placeholder="notes", id="e-notes")
                yield Checkbox("verified", value=bool(t.get("verified")), id="e-verified")
                yield Checkbox("enabled", value=bool(t.get("enabled", True)), id="e-enabled")
            with Horizontal(id="edit-buttons"):
                yield Button("Save", variant="primary", id="edit-save")
                yield Button("Cancel", id="edit-cancel")

    def on_mount(self) -> None:
        self.query_one("#e-expr", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-save":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        self.dismiss(
            {
                "expression": self.query_one("#e-expr", Input).value.strip(),
                "unit": self.query_one("#e-unit", Input).value.strip(),
                "min": self.query_one("#e-min", Input).value.strip(),
                "max": self.query_one("#e-max", Input).value.strip(),
                "notes": self.query_one("#e-notes", Input).value.strip(),
                "verified": self.query_one("#e-verified", Checkbox).value,
                "enabled": self.query_one("#e-enabled", Checkbox).value,
            }
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


# Central-log categories mapped to a display colour. Data-integrity faults
# (frames dropped/corrupted, bus errors, internal exceptions) are the loud red
# class; the softer non-answers (stale/no-data/decode) are yellow; anything else
# is dim. Categories are produced by canlib.uds_parse.classify_response +
# canlib.log.log_exception ("internal").
_EVENT_CATEGORY_STYLE = {
    "drop": "red",
    "bus": "red",
    "internal": "bold red",
    "stale": "yellow",
    "no_data": "yellow",
    "decode": "yellow",
}

# Round trip above which the health line reports it. Below this the link is not
# what is limiting a poll cycle and the number is only noise; above it, latency is
# the explanation for a slow or drop-prone session and the driver should see it.
_RTT_NOTABLE_S = 0.05


def _link_items(controller) -> list[StatusItem]:
    """Measured link round trip, once there is enough evidence to show one.

    Only shown when the link is slow enough to matter: on a LAN the number is
    noise, and every always-on segment costs a column a real signal could use.
    Tolerates a controller with no estimator (the raw client has none yet).
    """
    link_fn = getattr(controller, "link", None)
    link = link_fn() if callable(link_fn) else None
    rtt = getattr(link, "rtt", None) if link is not None else None
    if rtt is None or rtt < _RTT_NOTABLE_S:
        return []
    return [StatusItem(f"[dim]rtt[/] [yellow]{rtt * 1000:.0f}ms[/]", P_NORMAL)]


class EventLogModal(ModalScreen[None]):
    """Scrollable overlay of the central diagnostics event log.

    Snapshots the last N lines of ``canair logs`` (the same size-rotated file
    the transport-fault recorder writes drops/stale/timeouts/bus/decode and
    internal-exception events to) and colour-codes them by category. Read-only;
    reopen to refresh. The full history and management live in ``canair logs``.
    """

    _MAX_LINES = 200

    CSS = """
    EventLogModal { align: center middle; background: $background 60%; }
    #evlog-box {
        width: 90%; max-width: 140; height: 80%; max-height: 90%;
        padding: 1 2; border: round $accent; background: $surface;
    }
    #evlog-title { text-style: bold; margin-bottom: 1; }
    #evlog-rows { height: 1fr; }
    #evlog-hint { color: $text-muted; margin-top: 1; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "close"),
        Binding("q", "close", "close"),
        Binding("l", "close", "close"),
        Binding("j", "scroll_down", "down", show=False),
        Binding("k", "scroll_up", "up", show=False),
        Binding("g", "to_top", "top", show=False),
        Binding("G", "to_bottom", "bottom", show=False),
    ]

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical(id="evlog-box"):
            yield Label("Diagnostics event log (canair logs)", id="evlog-title")
            with VerticalScroll(id="evlog-rows"):
                yield Static(self._render_events(), id="evlog-body", markup=False)
            yield Label("newest last · j/k g/G scroll · l / esc / q to close", id="evlog-hint")

    def on_mount(self) -> None:
        # Land on the newest events (the file is oldest→newest).
        self.query_one("#evlog-rows", VerticalScroll).scroll_end(animate=False)

    def _render_events(self) -> Text:
        from ..log import parse_event_line, read_event_log

        lines = read_event_log(lines=self._MAX_LINES)
        if not lines:
            return Text("(no events logged yet)", style="dim")
        out = Text()
        for line in lines:
            fields = parse_event_line(line)
            category = fields.get("category", "")
            style = _EVENT_CATEGORY_STYLE.get(category, "dim")
            out.append(line, style=style)
            out.append("\n")
        return out

    def action_close(self) -> None:
        self.dismiss(None)

    def action_scroll_down(self) -> None:
        self.query_one("#evlog-rows", VerticalScroll).scroll_relative(y=1, animate=False)

    def action_scroll_up(self) -> None:
        self.query_one("#evlog-rows", VerticalScroll).scroll_relative(y=-1, animate=False)

    def action_to_top(self) -> None:
        self.query_one("#evlog-rows", VerticalScroll).scroll_home(animate=False)

    def action_to_bottom(self) -> None:
        self.query_one("#evlog-rows", VerticalScroll).scroll_end(animate=False)


def _keep_mode_label(keep_mode: str | None, keep_n: int | None) -> str:
    """Human-friendly description of a monitor keep/retain mode."""
    return {
        None: "none (latest only)",
        "changes": "changes (run-length dedup)",
        "unique": "unique (global dedup)",
        "all": "all (full time-series)",
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


class SessionInfoModal(ModalScreen[None]):
    """Read-only overlay: the run's session summary + closed-segment history.

    Shows the current segment's label/states/notes, the run-level counters
    (frames, cycles, retain mode, transport, start time), where captures and the
    ``--save`` write-ahead journal are being written, and a list of the ``--save``
    segments already finished this run (the ``n`` rotations). Renaming the current
    segment is done from here's hint via ``s`` (label) — this view is a read-only
    snapshot, reopen to refresh.
    """

    CSS = """
    SessionInfoModal { align: center middle; background: $background 60%; }
    #sess-box {
        width: 90%; max-width: 120; height: 80%; max-height: 90%;
        padding: 1 2; border: round $accent; background: $surface;
    }
    #sess-title { text-style: bold; margin-bottom: 1; }
    #sess-rows { height: 1fr; }
    #sess-hint { color: $text-muted; margin-top: 1; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "close"),
        Binding("q", "close", "close"),
        Binding("i", "close", "close"),
        Binding("j", "scroll_down", "down", show=False),
        Binding("k", "scroll_up", "up", show=False),
        Binding("g", "to_top", "top", show=False),
        Binding("G", "to_bottom", "bottom", show=False),
    ]

    def __init__(self, summary: dict, segments: list[dict]):
        super().__init__()
        self._summary = summary
        self._segments = segments

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical(id="sess-box"):
            yield Label("Session info", id="sess-title")
            with VerticalScroll(id="sess-rows"):
                yield Static(self._build_body(), id="sess-body", markup=False)
            yield Label(
                "s relabel · n new session · j/k g/G scroll · i / esc / q close", id="sess-hint"
            )

    def _build_body(self) -> Text:
        s = self._summary
        out = Text()

        def row(label: str, value: str) -> None:
            out.append(f"  {label:<16}", style="bold")
            out.append(f"{value}\n")

        rec = "● yes (--save)" if s.get("recording") else "no (not recording)"
        states = s.get("states") or []
        states_txt = ", ".join(states) if states else "(auto-detected on save)"
        notes = (s.get("notes") or "").replace("\n", " ").strip() or "—"

        out.append("Current segment\n", style="bold cyan")
        row("label", s.get("label") or "Monitor")
        row("recording", rec)
        row("states", states_txt)
        row("notes", notes)
        row("query", s.get("query") or "—")
        started = s.get("segment_started_at")
        seg_frames = s.get("segment_frames", 0)
        row("started", f"{_fmt_dt(started)}  ({seg_frames} frames)")

        out.append("\nRun summary\n", style="bold cyan")
        row("retain mode", _keep_mode_label(s.get("keep_mode"), s.get("keep_n")))
        row("view mode", str(s.get("view_mode", "full")))
        row("poll interval", f"{float(s.get('interval', 0.0)):.1f}s")
        row("cycles", str(s.get("cycle", 0)))
        row(
            "captured",
            f"{s.get('total_frames', 0)} frames · {s.get('unique_frames', 0)} unique",
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
                style="dim",
            )

        n = len(self._segments)
        out.append(f"\nFinished segments this run ({n})\n", style="bold cyan")
        if not n:
            out.append(
                "  (none yet — press n to finish this session and start a new one)\n",
                style="dim",
            )
        for i, seg in enumerate(self._segments, 1):
            label = seg.get("label") or "Monitor"
            span = f"{_fmt_dt(seg.get('started_at'))}→{_fmt_dt(seg.get('ended_at'))}"
            frames = seg.get("frames", 0)
            seg_states = ", ".join(seg.get("states") or []) or "—"
            written = seg.get("written")
            out.append(f"  {i}. ", style="bold")
            out.append(f"{label}\n")
            out.append(f"       {span} · {frames} frames · {seg_states}", style="dim")
            if written:
                out.append(f" → {written}", style="dim")
            out.append("\n")
        return out

    def on_mount(self) -> None:
        self.query_one("#sess-rows", VerticalScroll).scroll_home(animate=False)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_scroll_down(self) -> None:
        self.query_one("#sess-rows", VerticalScroll).scroll_relative(y=1, animate=False)

    def action_scroll_up(self) -> None:
        self.query_one("#sess-rows", VerticalScroll).scroll_relative(y=-1, animate=False)

    def action_to_top(self) -> None:
        self.query_one("#sess-rows", VerticalScroll).scroll_home(animate=False)

    def action_to_bottom(self) -> None:
        self.query_one("#sess-rows", VerticalScroll).scroll_end(animate=False)


class MonitorApp(HelpMixin, App):
    """Scrollable, in-place live-value monitor."""

    HELP_TITLE = "canair monitor — keyboard shortcuts"

    CSS = """
    Screen { layout: vertical; background: transparent; }
    #header { dock: top; height: auto; max-height: 3; padding: 0 1; background: transparent; }
    #scroll { height: 1fr; scrollbar-gutter: stable; scrollbar-size-vertical: 1; background: transparent; }
    #body { height: auto; padding: 0 1; background: transparent; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "quit"),
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
        Binding("question_mark", "help", "help"),
        Binding("s", "save", "save / label"),
        Binding("n", "new_segment", "new session"),
        Binding("r", "toggle_rulers", "byte ruler"),
        Binding("l", "event_log", "errors/log"),
        Binding("i", "session_info", "session info"),
        Binding("V", "cycle_view", "view mode"),
        Binding("space", "toggle_pause", "pause"),
        Binding("equals_sign", "faster", "poll faster"),
        Binding("plus", "faster", "poll faster", show=False),
        Binding("minus", "slower", "poll slower"),
        Binding("underscore", "slower", "poll slower", show=False),
        Binding("down", "select(1)", "select down", show=False, priority=True),
        Binding("up", "select(-1)", "select up", show=False, priority=True),
        Binding("escape", "clear_selection", "clear selection", show=False),
        Binding("e", "edit", "edit"),
        Binding("v", "verify", "verify"),
        Binding("d", "disable", "en/disable"),
        Binding("F", "cycle_filter", "filter"),
        Binding("R", "force_reconnect", "reconnect"),
        Binding("j", "scroll(1)", "down", show=False),
        Binding("k", "scroll(-1)", "up", show=False),
        Binding("g", "to_top", "top", show=False),
        Binding("G", "to_bottom", "bottom", show=False),
    ]

    def __init__(self, controller: MonitorController):
        super().__init__()
        self.controller = controller
        self.paused = False
        # Set when the user asks to quit, so an in-flight reconnect aborts cleanly.
        self._stopping = False
        # Transient status-line message (e.g. save confirmation) + its expiry.
        self._flash_msg = ""
        self._flash_expires = 0.0

    # -- layout ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("", id="header", markup=True)
        with VerticalScroll(id="scroll"):
            yield Static(self.controller.render(), id="body", markup=False)
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        # Use the terminal's own palette + default background (ansi_default)
        # rather than Textual's remapped truecolor theme + grey surface. Keeps
        # byte colours matching the plain output and readable on any themed
        # terminal (iTerm2, Termius, …). CSS `background: transparent` covers the
        # background regardless; the theme fixes the colour mapping.
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
        self.query_one("#scroll", VerticalScroll).focus()
        # Let the controller repaint mid-cycle as each PID resolves, so a slow /
        # timing-out PID never freezes the whole view (only its own row lags).
        self.controller._on_partial = self._refresh_body
        self.run_worker(self._poll_loop(), name="poll", exclusive=True)
        self.set_interval(0.25, self._update_status)
        self._update_status()
        self._update_header()

    # -- polling -----------------------------------------------------------
    async def action_quit(self) -> None:
        """Quit, signalling any in-flight reconnect to abort (clean stop)."""
        self._stopping = True
        self.exit()

    async def _poll_loop(self) -> None:
        while True:
            if not self.paused:
                t0 = time.monotonic()
                try:
                    await self.controller.poll_once()
                except transport_error_types() as e:
                    # The link died mid-cycle. Same handling as a poll that set
                    # `disconnected` itself: re-home rather than abandon the run.
                    log_event(
                        "bus",
                        f"poll cycle failed: {type(e).__name__}: {e}",
                        transport=self.controller.transport_type,
                    )
                    self.controller.disconnected = True
                except Exception as e:
                    # Not the transport — a genuine internal fault. Reconnecting
                    # would not fix it and would hide it, so stop cleanly rather
                    # than leaving a silently-frozen UI.
                    log_exception("unexpected monitor poll failure", e)
                    self.controller.disconnected = True
                    self.exit()
                    return
                if self.controller.disconnected:
                    # Try to re-home the dropped session (auto-failover / --wait)
                    # in place, keeping the --save journal recording across the gap.
                    if not await self._attempt_reconnect():
                        self.exit()
                        return
                    self._refresh_body()
                    continue
                self._refresh_body()
                remaining = self.controller.interval - (time.monotonic() - t0)
            else:
                remaining = 0.1
            # Chunked sleep so pause/quit stay responsive.
            deadline = time.monotonic() + max(remaining, 0.05)
            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)

    async def _attempt_reconnect(self) -> bool:
        """Re-home a dropped session; True to resume, False to exit the app.

        On a give-up the controller stays ``disconnected`` so ``mode_monitor``
        reports the drop; a user quit during the attempt clears it for a clean stop.
        """
        c = self.controller
        reconnect = getattr(c, "reconnect", None)
        if reconnect is None:
            return False

        def _notice(msg: str) -> None:  # thread-safe: plain attribute write
            c.reconnect_note = msg

        c.reconnecting = True
        c.reconnect_note = "connection dropped — reconnecting…"
        self._update_status()
        try:
            ok = await reconnect(
                c, getattr(c, "session_steps", None), stop=lambda: self._stopping, notice=_notice
            )
        finally:
            c.reconnecting = False
        if not ok and self._stopping:
            c.disconnected = False  # deliberate stop, not a failure
        return ok

    def _refresh_body(self) -> None:
        """Repaint the body in place, never moving the user's scroll position."""
        # The poll worker can fire mid-teardown (after quit); ignore if the DOM
        # is already gone.
        try:
            body = self.query_one("#body", Static)
        except NoMatches:
            return
        body.update(self.controller.render())
        self._update_status()

    def _update_status(self) -> None:
        try:
            status = self.query_one("#status", StatusBar)
        except NoMatches:
            return
        status.set_lines(self._metric_items(), self._hint_items())

    def _metric_items(self) -> list[StatusItem]:
        """First status line: what the run is doing right now, most vital first."""
        c = self.controller
        items: list[StatusItem] = []

        # Reconnecting banner (auto-failover / --wait): while a reconnect attempt
        # is in flight, lead the status line with its live note.
        if getattr(c, "reconnecting", False):
            note = getattr(c, "reconnect_note", "") or "reconnecting…"
            items.append(StatusItem(f"[b yellow]⟳ {note}[/]", P_ESSENTIAL))
        # Blinking ● REC while a --save journal is active. The dot pulses ~1s
        # on / 1s off, derived from wall-clock time so the rate is independent of
        # the status tick (0.25s); the label stays put so it doesn't jump layout.
        if getattr(c, "journal", None) is not None:
            dot = "[b red]●[/]" if int(time.monotonic()) % 2 == 0 else "[dim red]●[/]"
            items.append(StatusItem(f"{dot} [red]REC[/]", P_ESSENTIAL))
        if self.paused:
            items.append(StatusItem("[reverse] PAUSED [/]", P_ESSENTIAL))
        items.append(StatusItem(f"[dim]cycle[/] {c.cycle}", P_NORMAL))
        items.append(StatusItem(f"{c.interval:.1f}[dim]s[/]", P_NORMAL))
        items.append(StatusItem(f"{c.elapsed:.1f}[dim]s[/]", P_LOW))
        # ELM path reports commands + time spent in the ELM327; the raw path
        # reports UDS requests (no ELM involved).
        if getattr(c, "raw", False):
            items.append(StatusItem(f"{c.last_cmds}[dim] req[/]", P_LOW))
        else:
            items.append(
                StatusItem(
                    f"{c.last_cmds}[dim] cmds ·[/] {c.last_elm_time:.1f}[dim]s ELM[/]", P_LOW
                )
            )
        # Captured (all fresh payloads) vs unique (distinct values kept) — the two
        # differ from the on-screen row count (one row per polled PID).
        captured = getattr(c, "total_frames", 0)
        uniq = getattr(c, "unique_frames", 0)
        items.append(StatusItem(f"[dim]captured[/] {captured}[dim]/uniq[/] {uniq}", P_NORMAL))
        items.extend(self._health_items())
        # Live auto-suggested vehicle state (from decoded values), if any.
        state_fn = getattr(c, "suggested_state", None)
        if callable(state_fn):
            try:
                state = state_fn()
            except Exception:
                state = None
            if state:
                items.append(StatusItem(f"[dim]state[/] [cyan]{state}[/]", P_HIGH))
        if self._flash_msg:
            if time.monotonic() < self._flash_expires:
                items.append(StatusItem(f"[b green]{self._flash_msg}[/]", P_ESSENTIAL))
            else:
                self._flash_msg = ""
        return items

    def _health_items(self) -> list[StatusItem]:
        """Transport-health segments: dropped frames, pipe desyncs, other errors.

        Drops are the data-integrity headline (the reassembly corruption that
        silently poisoned historical captures), so a live burst also shows a
        per-cycle ``+N`` spike. ``stale`` is broken out rather than folded into
        drops because it means something different and actionable — replies
        landing in the wrong request's slot — and ``resync`` next to it shows the
        transport realigning itself, which is how a marginal link looks while it
        is still coping. All are omitted when zero, keeping a clean run
        uncluttered.
        """
        diag_fn = getattr(self.controller, "diag", None)
        diag = diag_fn() if callable(diag_fn) else None
        if diag is None:
            return []
        stale = getattr(diag, "stale", 0)
        drops = diag.drops - stale
        resyncs = getattr(diag, "resyncs", 0)
        # Non-drop errors (timeouts/bus/decode) — disjoint from drops and stale.
        errs = diag.errors - drops - stale
        items: list[StatusItem] = []
        if drops:
            spike = max(getattr(self.controller, "last_drops", 0) - self._last_stale(), 0)
            spike_txt = f" [b red]+{spike}[/]" if spike else ""
            items.append(StatusItem(f"[dim]drops[/] [b red]{drops}[/]{spike_txt}", P_HIGH))
        if stale:
            spike = self._last_stale()
            spike_txt = f" [b red]+{spike}[/]" if spike else ""
            items.append(StatusItem(f"[dim]stale[/] [b red]{stale}[/]{spike_txt}", P_HIGH))
        if resyncs:
            items.append(StatusItem(f"[dim]resync[/] [yellow]{resyncs}[/]", P_HIGH))
        if errs:
            items.append(StatusItem(f"[dim]errs[/] [yellow]{errs}[/]", P_HIGH))
        items.extend(_link_items(self.controller))
        return items

    def _last_stale(self) -> int:
        return getattr(self.controller, "last_stale", 0)

    def _editor(self):
        """The controller's edit collaborator, or None (older/fake controllers)."""
        return getattr(self.controller, "editor", None)

    def _update_header(self) -> None:
        """Render the top bar: current segment title + optional note/states.

        Title is the user-set label (falling back to the polled-selectors
        summary), so it's meaningful even without --save. The note line is
        omitted when empty, keeping the header a single line by default.
        """
        try:
            header = self.query_one("#header", Static)
        except NoMatches:
            return
        c = self.controller
        title_fn = getattr(c, "segment_title", None)
        title = title_fn() if callable(title_fn) else getattr(c, "session_label", "")
        states = getattr(c, "session_states", None) or []
        notes = (getattr(c, "session_notes", "") or "").replace("\n", " ").strip()

        line1 = f"[b cyan]{title or 'Monitor'}[/]"
        if states:
            line1 += f"  [dim]· {', '.join(states)}[/]"
        lines = [line1]
        if notes:
            width = max(20, (self.size.width or 80) - 4)
            if len(notes) > width:
                notes = notes[: width - 1] + "…"
            lines.append(f"[dim]{notes}[/]")
        header.update("\n".join(lines))

    def _hint_items(self) -> list[StatusItem]:
        """Second status line: the current selection/filter plus the key hints.

        Ranked so a narrow terminal keeps the escape hatches (``? help``,
        ``q quit``), the recording controls and the current context, and drops
        the browsing hints first — ``?`` is the complete key list either way.
        """
        c = self.controller
        # While recording (--save), `s` labels the session and `n` finishes it and
        # starts a new one; without --save, `s` is a one-off save and `n` is N/A.
        recording = getattr(c, "journal", None) is not None
        items: list[StatusItem] = []

        ed = self._editor()
        label = ed.selection_label() if ed is not None and hasattr(ed, "selection_label") else ""
        if label:
            items.append(StatusItem(f"[b]▶ {label}[/]", P_HIGH))
        if ed is not None:
            filt = getattr(ed, "filter_mode", "all")
            if filt != "all":
                items.append(StatusItem(f"[dim]filter[/] [cyan]{filt}[/]", P_HIGH))
        # Surface the active view mode when it's not the default full view.
        view = getattr(c, "view_mode", "full")
        if view and view != "full":
            items.append(StatusItem(f"[dim]view[/] [cyan]{view}[/]", P_HIGH))

        items.append(StatusItem(f"[dim]{'s label' if recording else 's save'}[/]", P_HIGH))
        if recording:
            items.append(StatusItem("[dim]n new-session[/]", P_HIGH))
        if ed is not None:
            items.append(StatusItem("[dim]↑↓ select[/]", P_NORMAL))
            items.append(StatusItem("[dim]e edit[/]", P_NORMAL))
            items.append(StatusItem("[dim]v verify[/]", P_LOW))
            items.append(StatusItem("[dim]d en/disable[/]", P_LOW))
            items.append(StatusItem("[dim]F filter[/]", P_LOW))
        else:
            items.append(StatusItem("[dim]↑↓/jk PgUp/PgDn g/G[/]", P_LOW))
        # The keys nothing else hints at (a view/ruler/overlay) rank above the
        # ones a user would try anyway (space to pause, arrows to scroll).
        items.append(StatusItem("[dim]V view[/]", P_NORMAL))
        items.append(StatusItem("[dim]r ruler[/]", P_NORMAL))
        items.append(StatusItem("[dim]i info[/]", P_NORMAL))
        items.append(StatusItem("[dim]l errors[/]", P_LOW))
        items.append(StatusItem("[dim]space pause[/]", P_LOW))
        if label:
            items.append(StatusItem("[dim]esc deselect[/]", P_LOW))
        items.append(StatusItem("[dim]? help[/]", P_ESSENTIAL))
        items.append(StatusItem("[dim]q quit[/]", P_ESSENTIAL))
        return items

    # -- actions -----------------------------------------------------------
    def action_scroll(self, delta: int) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_relative(y=delta, animate=False)

    def action_to_top(self) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_home(animate=False)

    def action_to_bottom(self) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_end(animate=False)

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self._update_status()

    # Live poll-rate control. The poll loop reads controller.interval each cycle,
    # so mutating it takes effect on the next sleep. Clamped to a sane range.
    _MIN_INTERVAL = 0.1
    _MAX_INTERVAL = 300.0

    def action_faster(self) -> None:
        self._set_interval(max(self._MIN_INTERVAL, round(self.controller.interval / 1.5, 1)))

    def action_slower(self) -> None:
        self._set_interval(min(self._MAX_INTERVAL, round(self.controller.interval * 1.5, 1)))

    def _set_interval(self, value: float) -> None:
        self.controller.interval = value
        self._flash(f"Poll interval: {value:.1f}s", secs=2.0)

    def action_toggle_rulers(self) -> None:
        """Toggle the byte-index ruler + the per-signal byte-reference column."""
        self.controller.show_rulers = not self.controller.show_rulers
        self._refresh_body()

    def action_event_log(self) -> None:
        """Open the scrollable diagnostics event-log overlay."""
        if self._modal_active():
            return
        self.push_screen(EventLogModal())

    def action_session_info(self) -> None:
        """Open the session summary + segment-history overlay."""
        if self._modal_active():
            return
        c = self.controller
        summary_fn = getattr(c, "session_summary", None)
        summary = summary_fn() if callable(summary_fn) else {}
        history_fn = getattr(c, "segment_history", None)
        segments = history_fn() if callable(history_fn) else []
        self.push_screen(SessionInfoModal(summary, segments))

    def action_cycle_view(self) -> None:
        """Cycle the display view mode (ecus → ranges → signals → full)."""
        if self._modal_active():
            return
        cycle_fn = getattr(self.controller, "cycle_view", None)
        if not callable(cycle_fn):
            return
        mode = cycle_fn()
        self._refresh_body()
        self._flash(f"View: {mode}")

    # -- selection / in-place editing --------------------------------------
    def _last_queries(self):
        return getattr(self.controller, "last_queries", [])

    def _modal_active(self) -> bool:
        """True when a dialog owns the screen (don't act on the view beneath it)."""
        return len(self.screen_stack) > 1

    def action_select(self, delta: int) -> None:
        """Move the signal-selection cursor and scroll it into view.

        Falls back to plain scrolling when there is no editor or nothing is
        selectable, so the arrow keys never feel dead.
        """
        if self._modal_active():
            return
        ed = self._editor()
        if ed is None or ed.move(self._last_queries(), delta) is None:
            self.action_scroll(delta)
            return
        self._refresh_body()
        self._scroll_to_selection()
        self._update_status()

    def action_clear_selection(self) -> None:
        """Drop the ▶ signal cursor so ↑/↓ resume plain scrolling.

        No-ops (letting escape fall through) when a modal owns the screen or
        nothing is selected, so it never steals escape from a dialog.
        """
        if self._modal_active():
            return
        ed = self._editor()
        if ed is None or not getattr(ed, "clear_selection", None):
            return
        if not ed.clear_selection():
            return
        self._refresh_body()
        self._flash("Selection cleared.")

    def action_cycle_filter(self) -> None:
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        mode = ed.cycle_filter(self._last_queries())
        self._refresh_body()
        self._flash(f"Filter: {mode}")

    def action_force_reconnect(self) -> None:
        """Ask the poll loop to rebuild the connection after the current cycle.

        Deliberately only sets the flag the loop already watches instead of
        reconnecting here: an attempt started from a key handler would race the
        in-flight poll for the same terminal.
        """
        c = self.controller
        if self._modal_active() or c.reconnecting:
            return
        if getattr(c, "reconnect", None) is None:
            self._flash("Reconnect unavailable (no reconnector for this session)")
            return
        c.disconnected = True
        self._flash("Reconnecting after this poll cycle…")

    def action_verify(self) -> None:
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        if getattr(ed, "selected", None) is None:
            self._flash("Select a signal first (↑↓).")
            return
        self._flash(ed.toggle_verified())
        self._refresh_body()

    def action_disable(self) -> None:
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        if getattr(ed, "selected", None) is None:
            self._flash("Select a signal first (↑↓).")
            return
        self._flash(ed.toggle_enabled())
        self._refresh_body()

    def action_edit(self) -> None:
        """Open the edit dialog for the selected signal (polling pauses)."""
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        target = ed.edit_target() if hasattr(ed, "edit_target") else None
        if not target:
            self._flash("Select a signal first (↑↓).")
            return
        # Auto-pause polling while the modal is open; restore prior state after.
        was_paused = self.paused
        self.paused = True

        def _done(result: dict | None) -> None:
            self.paused = was_paused
            if result is None:
                self._flash("Edit cancelled.")
                return
            try:
                msg = ed.apply_edit(result)
            except Exception as exc:  # keep the TUI alive on any edit error
                msg = f"Edit failed: {exc}"
            self._flash(msg)
            self._refresh_body()

        self.push_screen(EditParamDialog(target), _done)

    def _scroll_to_selection(self) -> None:
        """Scroll so the ``▶`` selection cursor is within the viewport."""
        try:
            scroll = self.query_one("#scroll", VerticalScroll)
            body = self.query_one("#body", Static)
        except NoMatches:
            return
        reveal_marker(scroll, body)

    def _flash(self, msg: str, secs: float = 5.0) -> None:
        """Show a transient message in the status line for ``secs`` seconds."""
        self._flash_msg = msg
        self._flash_expires = time.monotonic() + secs
        self._update_status()

    def _suggested_metadata(self) -> tuple[str, str, list[tuple[str, str]] | None]:
        """Gather (suggested_label, suggested_state, state_options) for a dialog.

        Label pre-fills from the active query (e.g. "BCM VCU:2101"), falling back
        to a timestamp; state is the profile's auto-suggestion from decoded values.
        """
        suggested = ""
        label_fn = getattr(self.controller, "query_label", None)
        if callable(label_fn):
            suggested = label_fn()
        if not suggested:
            suggested = f"Monitor {datetime.now():%H:%M}"

        suggested_state = ""
        state_fn = getattr(self.controller, "suggested_states", None)
        if callable(state_fn):
            try:
                from ..states import join_states

                suggested_state = join_states(state_fn())
            except Exception:
                suggested_state = ""

        state_options = None
        options_fn = getattr(self.controller, "state_options", None)
        if callable(options_fn):
            try:
                state_options = options_fn()
            except Exception:
                state_options = None
        return suggested, suggested_state, state_options

    def action_save(self) -> None:
        """Label the recording (--save) or write captures now (no --save).

        With --save active every payload is already journaled and written to a
        capture file automatically on exit, so ``s`` only *labels* the current
        recording (label / state / notes). Without --save it performs an on-demand
        write of the payloads captured so far to a new capture file.
        """
        if not self.controller.has_captures():
            self._flash("No payloads captured yet — nothing to save.")
            return
        suggested, suggested_state, state_options = self._suggested_metadata()
        recording = getattr(self.controller, "journal", None) is not None
        if recording:
            title = "Label recording"
            caption = (
                "This recording is [b]already being saved[/] — every payload is "
                "written automatically when you quit or start a new session.\n"
                "[dim]Here you only set its label / state / notes. "
                "Use [b]n[/b] to finish this session and start a fresh one.[/]"
            )
            save_button = "Set label"
        else:
            title = "Save captures now"
            caption = (
                "Writes the payloads captured so far to a [b]new capture file[/].\n"
                "[dim](Not recording with --save — this is a one-off snapshot save.)[/]"
            )
            save_button = "Save"

        def _done(result: tuple[str, str, str] | None) -> None:
            if result is None:
                self._flash("Cancelled — nothing saved.")
                return
            label, state, notes = result
            try:
                msg = self.controller.save_now(label, state, notes)
            except Exception as exc:  # keep the TUI alive on any save error
                msg = f"Save failed: {exc}"
            self._flash(msg)
            self._update_header()

        self.push_screen(
            SaveDialog(
                suggested,
                suggested_state,
                state_options,
                title=title,
                caption=caption,
                save_button=save_button,
            ),
            _done,
        )

    def action_new_segment(self) -> None:
        """Finish the current --save session (write it now) and start a fresh one."""
        if getattr(self.controller, "journal", None) is None:
            self._flash("Start a new session needs --save (nothing is being recorded).")
            return
        suggested, suggested_state, state_options = self._suggested_metadata()

        def _done(result: tuple[str, str, str] | None) -> None:
            if result is None:
                self._flash("Cancelled — current session kept recording.")
                return
            label, state, notes = result
            try:
                msg = self.controller.new_segment(label, state, notes)
            except Exception as exc:  # keep the TUI alive on any error
                msg = f"Could not start new session: {exc}"
            self._flash(msg)
            self._update_header()

        self.push_screen(
            SaveDialog(
                suggested,
                suggested_state,
                state_options,
                title="Finish session & start a new one",
                caption=(
                    "[b]Writes the current session to a capture file now[/], then "
                    "starts a fresh recording with the label below.\n"
                    "[dim]One monitor run can produce several separately-labelled "
                    "sessions this way.[/]"
                ),
                save_button="Finish & start new",
            ),
            _done,
        )


async def run_monitor_app(controller: MonitorController) -> None:
    """Run the monitor TUI to completion (returns when the user quits).

    Stop signals (``SIGTERM``/``SIGHUP`` — a `kill`, a vanished terminal, or the
    lock watchdog standing down) are routed through the app's own quit action, so
    the TUI tears itself down cleanly (terminal restored, ``--save`` journal
    reconciled) instead of a ``KeyboardInterrupt`` unwinding through Textual's
    internals.
    """
    from ..stop_signals import graceful_stop_async

    app = MonitorApp(controller)

    def _request_quit() -> None:
        # Runs on the event loop (asyncio signal handler), so the app can be
        # asked to exit directly; _stopping aborts any in-flight reconnect.
        app._stopping = True
        app.exit()

    with graceful_stop_async(_request_quit):
        await app.run_async()
