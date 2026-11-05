"""Status bar and header content for the monitor screen: what each line says.

Each function takes the controller (and the app flags that are not its state) and returns
:class:`~canair.tui.status.StatusItem` rows or the header markup. They read the controller
and never touch the widget tree, so a test passes a stub.
"""

from __future__ import annotations

import time

from rich.markup import escape

from canair.live.monitor.backoff import summarize_slowed
from canair.render.glyphs import CURSOR, ELLIPSIS
from canair.tui import styles
from canair.tui.keys import ARROWS, PAGE_HINT, arrows_hint, key_hint
from canair.tui.status import (
    P_ESSENTIAL,
    P_HIGH,
    P_LOW,
    P_NORMAL,
    StatusItem,
    hint_item,
)

#: The hint for the ECU/PID picker key, shown in the header and the status bar.
PICK_HINT = key_hint("pick", "select ECUs/PIDs")
SELECT_FIRST = f"Select a signal first with {ARROWS}"


# Round trip above which the health line reports it. Below this the link is not
# what is limiting a poll cycle and the number is only noise; above it, latency is
# the explanation for a slow or drop-prone session and the driver should see it.
RTT_NOTABLE_S = 0.05


def link_items(controller) -> list[StatusItem]:
    """Measured link round trip, once there is enough evidence to show one.

    Only shown when the link is slow enough to matter: on a LAN the number is
    noise, and every always-on segment costs a column a real signal could use.
    Tolerates a controller with no estimator (the raw client has none yet).
    """
    link_fn = getattr(controller, "link", None)
    link = link_fn() if callable(link_fn) else None
    rtt = getattr(link, "rtt", None) if link is not None else None
    if rtt is None or rtt < RTT_NOTABLE_S:
        return []
    return [StatusItem(f"[dim]rtt[/] [{styles.WARNING}]{rtt * 1000:.0f}ms[/]", P_NORMAL)]


def metric_items(c, *, paused: bool, flash: StatusItem | None) -> list[StatusItem]:
    """First status line: what the run is doing right now, most vital first."""
    items: list[StatusItem] = []

    # Reconnecting banner (auto-failover / --wait): while a reconnect attempt
    # is in flight, lead the status line with its live note.
    if getattr(c, "reconnecting", False):
        note = getattr(c, "reconnect_note", "") or f"reconnecting{ELLIPSIS}"
        items.append(StatusItem(f"[b {styles.WARNING}]⟳ {note}[/]", P_ESSENTIAL))
    # Blinking ● REC while a --save journal is active. The dot pulses ~1s
    # on / 1s off, derived from wall-clock time so the rate is independent of
    # the status tick (0.25s); the label stays put so it doesn't jump layout.
    # Bold, not red: red means an error.
    if c.recorder.journal is not None:
        dot_style = styles.RECORDING if int(time.monotonic()) % 2 == 0 else styles.MUTED
        items.append(StatusItem(f"[{dot_style}]●[/] [{styles.RECORDING}]REC[/]", P_ESSENTIAL))
    if paused:
        items.append(StatusItem("[reverse] PAUSED [/]", P_ESSENTIAL))
    items.append(StatusItem(f"[dim]cycle[/] {c.cycle}", P_NORMAL))
    items.append(StatusItem(f"{c.schedule.describe()}", P_NORMAL))
    backoff = getattr(c, "backoff", None)
    slowed = backoff.slowed() if backoff is not None else {}
    if slowed:
        items.append(
            StatusItem(f"[dim]slow[/] [{styles.WARNING}]{summarize_slowed(slowed)}[/]", P_HIGH)
        )
    items.append(StatusItem(f"{c.elapsed:.1f}[dim]s[/]", P_LOW))
    # ELM path reports commands + time spent in the ELM327; the raw path
    # reports UDS requests (no ELM involved).
    if getattr(c, "raw", False):
        items.append(StatusItem(f"{c.last_cmds}[dim] req[/]", P_LOW))
    else:
        items.append(
            StatusItem(f"{c.last_cmds}[dim] cmds ·[/] {c.last_elm_time:.1f}[dim]s ELM[/]", P_LOW)
        )
    # Captured (all fresh payloads) vs unique (distinct values kept) — the two
    # differ from the on-screen row count (one row per polled PID).
    captured = c.recorder.total_frames
    uniq = c.recorder.unique_frames
    items.append(StatusItem(f"[dim]captured[/] {captured}[dim]/uniq[/] {uniq}", P_NORMAL))
    items.extend(health_items(c))
    items.extend(session_items(c))
    # Live auto-suggested vehicle state (from decoded values), if any. One slot,
    # so this is the MOST SPECIFIC matching state (DRIVING, not the READY it
    # implies) — see MonitorStateSuggester.suggested_state.
    try:
        state = c.state.suggested_state()
    except Exception:
        state = None
    if state:
        items.append(StatusItem(f"[dim]state[/] [b]{state}[/]", P_HIGH))
    if flash is not None:
        items.append(flash)
    return items


def health_items(controller) -> list[StatusItem]:
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
    diag_fn = getattr(controller, "diag", None)
    diag = diag_fn() if callable(diag_fn) else None
    if diag is None:
        return []
    last_stale = getattr(controller, "last_stale", 0)
    stale = getattr(diag, "stale", 0)
    drops = diag.drops - stale
    resyncs = getattr(diag, "resyncs", 0)
    # Non-drop errors (timeouts/bus/decode) — disjoint from drops and stale.
    errs = diag.errors - drops - stale
    items: list[StatusItem] = []
    if drops:
        spike = max(getattr(controller, "last_drops", 0) - last_stale, 0)
        spike_txt = f" [b {styles.ERROR}]+{spike}[/]" if spike else ""
        items.append(StatusItem(f"[dim]drops[/] [b {styles.ERROR}]{drops}[/]{spike_txt}", P_HIGH))
    if stale:
        spike = last_stale
        spike_txt = f" [b {styles.ERROR}]+{spike}[/]" if spike else ""
        items.append(StatusItem(f"[dim]stale[/] [b {styles.ERROR}]{stale}[/]{spike_txt}", P_HIGH))
    if resyncs:
        items.append(StatusItem(f"[dim]resync[/] [{styles.WARNING}]{resyncs}[/]", P_HIGH))
    if errs:
        items.append(StatusItem(f"[dim]errs[/] [{styles.WARNING}]{errs}[/]", P_HIGH))
    items.extend(link_items(controller))
    return items


def session_items(controller) -> list[StatusItem]:
    """Diagnostic session-type segment: which ECUs have a ``10 xx`` session open.

    One combined item (not one per ECU) to stay compact on a multi-ECU
    query. Omitted entirely when nothing is tracked — e.g. before any
    ``session`` step has run at all. Press ``t`` to view/switch.
    """
    modes = controller.sessions.active_modes()
    if not modes:
        return []
    parts = ", ".join(f"{ecu}:{mode}" for ecu, mode in sorted(modes.items()))
    return [StatusItem(f"[dim]sess[/] [b]{parts}[/]", P_NORMAL)]


def header_text(c, width: int) -> str:
    """The top bar: current segment title, selected states and the picker hint, plus an optional note.

    The title is the user-set label (falling back to the polled-selectors summary), so it is
    meaningful even without --save. The note line is omitted when empty, keeping the header a
    single line by default.
    """
    title = c.view.segment_title()
    states = c.recorder.session_states
    notes = c.recorder.session_notes.replace("\n", " ").strip()

    line1 = f"[b]{escape(title or 'Monitor')}[/]"
    if states:
        line1 += f"  [dim]· {', '.join(states)}[/]"
    line1 += f"  [dim]·[/] [b]{PICK_HINT}[/]"
    lines = [line1]
    if notes:
        width = max(20, (width or 80) - 4)
        if len(notes) > width:
            notes = notes[: width - 1] + "…"
        lines.append(f"[dim]{notes}[/]")
    return "\n".join(lines)


def hint_items(c) -> list[StatusItem]:
    """Second status line: the current selection/filter plus the key hints.

    Ranked so a narrow terminal keeps the escape hatches (``? help``,
    ``q quit``), the recording controls and the current context, and drops
    the browsing hints first — ``?`` is the complete key list either way.
    """
    # While recording (--save), `s` labels the session and `n` finishes it and
    # starts a new one; without --save, `s` is a one-off save and `n` is N/A.
    recording = c.recorder.journal is not None
    items: list[StatusItem] = []

    ed = getattr(c, "editor", None)
    label = ed.selection_label() if ed is not None and hasattr(ed, "selection_label") else ""
    if label:
        items.append(StatusItem(f"[b]{CURSOR} {escape(label)}[/]", P_HIGH))
    if ed is not None:
        filt = getattr(ed, "filter_mode", "all")
        if filt != "all":
            items.append(StatusItem(f"[dim]filter[/] [b]{filt}[/]", P_HIGH))
    # Surface the active view mode when it's not the default full view.
    view = getattr(c, "view_mode", "full")
    if view and view != "full":
        items.append(StatusItem(f"[dim]view[/] [b]{view}[/]", P_HIGH))

    # Changing what is polled is the key a newcomer most needs and would not
    # guess, so it leads the hints, undimmed, and outlives the other keys.
    items.append(StatusItem(f"[b]{PICK_HINT}[/]", P_HIGH))
    items.append(hint_item(key_hint("session", "label" if recording else "save"), P_HIGH))
    if recording:
        items.append(hint_item(key_hint("new", "new session"), P_HIGH))
    if ed is not None:
        items.append(hint_item(arrows_hint("select"), P_NORMAL))
        items.append(hint_item(key_hint("edit"), P_NORMAL))
        items.append(hint_item(key_hint("verify"), P_LOW))
        items.append(hint_item(key_hint("exclude", "toggle ship"), P_LOW))
        items.append(hint_item(key_hint("filter_cycle"), P_LOW))
    else:
        items.append(hint_item(arrows_hint("scroll"), P_LOW))
        items.append(hint_item(PAGE_HINT, P_LOW))
    # The keys nothing else hints at (a view/ruler/overlay) rank above the
    # ones a user would try anyway (space to pause, arrows to scroll).
    items.append(hint_item(key_hint("view"), P_NORMAL))
    items.append(hint_item(key_hint("rulers", "byte ruler"), P_NORMAL))
    items.append(hint_item(key_hint("info", "session info"), P_NORMAL))
    items.append(hint_item(key_hint("value_type", "session type"), P_NORMAL))
    items.append(hint_item(key_hint("event_log"), P_LOW))
    items.append(hint_item(key_hint("pause"), P_LOW))
    if label:
        items.append(hint_item(key_hint("back", "deselect"), P_LOW))
    items.append(hint_item(key_hint("help"), P_ESSENTIAL))
    items.append(hint_item(key_hint("quit"), P_ESSENTIAL))
    return items
