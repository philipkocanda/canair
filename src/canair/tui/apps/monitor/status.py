"""What the monitor's bars say: the status zones, the menu bar's title and the bottom line.

Each function reads the controller (and the app flags that are not its state) and returns
:class:`~canair.tui.status.StatusItem` facts or :class:`~canair.tui.shell.statusline.Zone`
groups of them; the shell lays them out for the terminal's width. They never touch the widget
tree, so a test passes a stub.

The status line has four zones, always in this order:

* **link** — the link's verdict (``✓ link``, ``⚠ 2 resync``, ``✗ 3 drops``, ``RECONNECTING``),
  the device host, the transport and a round trip worth knowing about;
* **poll** — ``LIVE`` or ``PAUSED``, the cycle, the schedule, the PIDs polled less often;
* **recording** — ``● REC`` and how long it has run, the frames captured;
* **car** (pinned right) — an output left on, the vehicle state, the diagnostic sessions.

Only an exception is a badge (a reversed block); the normal state is plain text.
"""

from __future__ import annotations

import time
from datetime import datetime

from rich.markup import escape

from canair.live.monitor.backoff import summarize_slowed
from canair.render.glyphs import CURSOR, FAIL, OK, WARN
from canair.tui import styles
from canair.tui.keys import ARROWS, arrows_hint, key_hint
from canair.tui.shell.statusline import Zone, badge
from canair.tui.status import P_ESSENTIAL, P_HIGH, P_LOW, P_NORMAL, StatusItem, hint_item
from canair.uds.services import DIAGNOSTIC_SESSION_CONTROL
from canair.uds.subfunctions import parse_session_type, subfunction_name

SELECT_FIRST = f"Select a signal first with {ARROWS}"

# Round trip above which the link zone reports it. Below this the link is not what is limiting a
# poll cycle and the number is only noise; above it, latency is the explanation for a slow or
# drop-prone session and the driver should see it.
RTT_NOTABLE_S = 0.05


# -- link ---------------------------------------------------------------------------------
def link_items(controller) -> list[StatusItem]:
    """Measured link round trip, once there is enough evidence and it is slow enough to matter.

    Tolerates a controller with no estimator (the raw client has none yet).
    """
    link_fn = getattr(controller, "link", None)
    link = link_fn() if callable(link_fn) else None
    rtt = getattr(link, "rtt", None) if link is not None else None
    if rtt is None or rtt < RTT_NOTABLE_S:
        return []
    return [StatusItem(f"[{styles.WARNING}]rtt {rtt * 1000:.0f}ms[/]", P_NORMAL)]


def health_items(controller) -> list[StatusItem]:
    """Transport-health facts: dropped frames, pipe desyncs, other errors, a slow round trip.

    Drops are the data-integrity headline (the reassembly corruption that silently poisoned
    historical captures), so they are badges, and a live burst also shows the cycle's ``+N``.
    ``stale`` is broken out rather than folded into drops because it means something different
    and actionable — replies landing in the wrong request's slot — and ``resync`` next to it
    shows the transport realigning itself, which is how a marginal link looks while it is still
    coping. All are omitted when zero, keeping a clean run uncluttered.
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
        text = f"{FAIL} {drops} drops" + (f" +{spike}" if spike else "")
        items.append(StatusItem(badge(text, styles.ERROR), P_ESSENTIAL))
    if stale:
        text = f"{FAIL} {stale} stale" + (f" +{last_stale}" if last_stale else "")
        items.append(StatusItem(badge(text, styles.ERROR), P_ESSENTIAL))
    if resyncs:
        items.append(StatusItem(f"[{styles.WARNING}]{WARN} {resyncs} resync[/]", P_HIGH))
    if errs:
        items.append(StatusItem(f"[{styles.WARNING}]{WARN} {errs} errs[/]", P_HIGH))
    items.extend(link_items(controller))
    return items


def link_zone(c) -> Zone:
    items: list[StatusItem] = []
    if getattr(c, "reconnecting", False):
        items.append(StatusItem(badge("⟳ RECONNECTING", styles.WARNING), P_ESSENTIAL))
        note = getattr(c, "reconnect_note", "")
        if note:
            items.append(StatusItem(f"[{styles.WARNING}]{escape(note)}[/]", P_LOW))
    health = health_items(c)
    if not items and not any(it.priority >= P_HIGH for it in health):
        items.append(StatusItem(f"[{styles.OK}]{OK} link[/]", P_HIGH))
    items.extend(health)
    host_fn = getattr(c, "device_host", None)
    host = host_fn() if callable(host_fn) else None
    if host:
        items.append(StatusItem(f"[{styles.NAME}]{escape(host)}[/]", P_LOW + 2))
    transport = getattr(c, "transport_type", None)
    if transport:
        items.append(StatusItem(f"[{styles.MUTED}]{escape(transport)}[/]", P_LOW))
    return Zone("link", tuple(items))


# -- poll ---------------------------------------------------------------------------------
def poll_zone(c, *, paused: bool) -> Zone:
    items = [
        StatusItem(badge("PAUSED", styles.WARNING), P_ESSENTIAL)
        if paused
        else StatusItem(f"[{styles.OK}]LIVE[/]", P_HIGH),
        StatusItem(f"[{styles.MUTED}]#[/]{c.cycle}", P_NORMAL + 2),
    ]
    schedule = c.schedule.describe()
    fixed = getattr(c.schedule, "schedule", "fixed") == "fixed"
    items.append(
        StatusItem(f"[{styles.MUTED}]every[/] {schedule}" if fixed else schedule, P_NORMAL)
    )
    backoff = getattr(c, "backoff", None)
    slowed = backoff.slowed() if backoff is not None else {}
    if slowed:
        items.append(
            StatusItem(f"[{styles.WARNING}]slow {escape(summarize_slowed(slowed))}[/]", P_HIGH)
        )
    # The ELM path reports commands and time spent in the ELM327; the raw path UDS requests.
    if getattr(c, "raw", False):
        cost = f"{c.last_cmds}[{styles.MUTED}] req[/]"
    else:
        cost = f"{c.last_cmds}[{styles.MUTED}] cmds ·[/] {c.last_elm_time:.1f}[{styles.MUTED}]s[/]"
    items.append(StatusItem(f"{c.elapsed:.1f}[{styles.MUTED}]s ·[/] {cost}", P_LOW - 1))
    return Zone("poll", tuple(items))


# -- recording ----------------------------------------------------------------------------
def _duration(since: object) -> str:
    if not isinstance(since, datetime):
        return ""
    secs = max(0, int((datetime.now() - since).total_seconds()))
    hours, rest = divmod(secs, 3600)
    mins, secs = divmod(rest, 60)
    return f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"


def record_zone(c) -> Zone:
    r = c.recorder
    items: list[StatusItem] = []
    if r.journal is not None:
        # The dot pulses ~1s on / 1s off from wall-clock time, so the rate does not follow the
        # status tick, and the label stays put so the line does not jump. Bold, not red: red
        # means an error.
        dot = styles.RECORDING if int(time.monotonic()) % 2 == 0 else styles.MUTED
        items.append(StatusItem(f"[{dot}]●[/] [{styles.RECORDING}]REC[/]", P_ESSENTIAL - 1))
        elapsed = _duration(getattr(r, "segment_started_at", None))
        if elapsed:
            items.append(StatusItem(elapsed, P_NORMAL))
    else:
        items.append(StatusItem(f"[{styles.MUTED}]not recording[/]", P_LOW))
    # Captured (all fresh payloads) vs unique (distinct values kept): both differ from the
    # on-screen row count (one row per polled PID).
    items.append(
        StatusItem(
            f"{r.total_frames:,}[{styles.MUTED}] frames ·[/] {r.unique_frames:,}"
            f"[{styles.MUTED}] unique[/]",
            P_LOW,
        )
    )
    return Zone("record", tuple(items))


# -- car ----------------------------------------------------------------------------------
def session_word(mode: str) -> str:
    """``81`` → ``81 standard``: a session type with the short form of its UDS/KWP name."""
    try:
        name = subfunction_name(DIAGNOSTIC_SESSION_CONTROL, parse_session_type(mode)) or ""
    except ValueError:
        return mode
    short = name.removesuffix("DiagnosticSession").removesuffix("Session")
    return f"{mode} {short}" if short else mode


def car_zone(c) -> Zone:
    items: list[StatusItem] = []
    # An output this run switched on is the car's state, not the monitor's: it outlives every
    # other fact on the line.
    actuation = getattr(c, "actuation", None)
    on = actuation.on_count() if actuation is not None else 0
    if on:
        items.append(StatusItem(badge(f"{WARN} {on} ON", styles.WARNING), P_ESSENTIAL + 5))
    # One slot, so the MOST SPECIFIC matching state (DRIVING, not the READY it implies).
    try:
        state = c.state.suggested_state()
    except Exception:
        state = None
    if state:
        items.append(StatusItem(f"[{styles.HEADING}]{escape(state)}[/]", P_HIGH))
    modes = c.sessions.active_modes()
    if modes:
        sessions = " [dim]·[/] ".join(
            f"[{styles.NAME}]{escape(ecu)}[/] {escape(session_word(mode))}"
            for ecu, mode in sorted(modes.items())
        )
        items.append(StatusItem(sessions, P_NORMAL - 2))
    return Zone("car", tuple(items))


def status_zones(c, *, paused: bool) -> tuple[list[Zone], Zone]:
    """The left zones in order, and the zone pinned to the right edge."""
    return [link_zone(c), poll_zone(c, paused=paused), record_zone(c)], car_zone(c)


# -- the menu bar's title -------------------------------------------------------------------
def document_items(c) -> list[StatusItem]:
    """The recording's label, its states and notes, the profile: what the menu bar shows right.

    The menu bar joins them with ``·`` and drops the least important first.
    """
    items = [StatusItem(f"[b]{escape(c.view.segment_title() or 'Monitor')}[/]", P_HIGH)]
    states = c.recorder.session_states
    if states:
        items.append(StatusItem(escape(", ".join(states)), P_NORMAL))
    notes = (c.recorder.session_notes or "").replace("\n", " ").strip()
    if notes:
        items.append(StatusItem(f"[{styles.MUTED}]{escape(notes)}[/]", P_LOW))
    profile = getattr(getattr(c, "profile", None), "name", None)
    if profile:
        items.append(StatusItem(f"[{styles.MUTED}]{escape(profile)}[/]", P_LOW - 1))
    return items


# -- the bottom line ------------------------------------------------------------------------
def actuator_context() -> list[StatusItem]:
    """The keys of the actuator pane while it has focus, then the way back."""
    return [
        hint_item(key_hint("accept", "toggle"), P_HIGH),
        hint_item(key_hint("actuate_off"), P_HIGH),
        hint_item(key_hint("set_value"), P_NORMAL),
        hint_item(key_hint("start_routine"), P_NORMAL),
        hint_item(key_hint("stop_routine"), P_LOW),
        hint_item(key_hint("block_next", "next pane"), P_NORMAL),
    ]


def context_items(c, *, actuators: bool = False) -> list[StatusItem]:
    """The bottom line's left side: modes that are not the default, the selection, its keys.

    With nothing selected it shows the keys a newcomer most needs and would not guess.
    """
    items: list[StatusItem] = []
    view = getattr(c, "view_mode", "full")
    if view and view != "full":
        items.append(StatusItem(f"[{styles.MUTED}]view[/] [b]{escape(view)}[/]", P_HIGH))
    ed = getattr(c, "editor", None)
    filt = getattr(ed, "filter_mode", "all") if ed is not None else "all"
    if filt != "all":
        items.append(StatusItem(f"[{styles.MUTED}]filter[/] [b]{escape(filt)}[/]", P_HIGH))
    if getattr(c, "show_rulers", False):
        items.append(StatusItem("[b]ruler[/]", P_NORMAL))
    if actuators:
        return items + actuator_context()
    label = ed.selection_label() if ed is not None and hasattr(ed, "selection_label") else ""
    if label:
        items.append(StatusItem(f"[b]{CURSOR} {escape(label)}[/]", P_HIGH + 2))
        items += [
            hint_item(key_hint("edit"), P_NORMAL),
            hint_item(key_hint("verify"), P_NORMAL - 1),
            hint_item(key_hint("exclude", "toggle ship"), P_LOW),
            hint_item(key_hint("back", "deselect"), P_LOW - 1),
        ]
        return items
    recording = c.recorder.journal is not None
    if ed is not None:
        items.append(hint_item(arrows_hint("select"), P_NORMAL))
    items += [
        hint_item(key_hint("pick", "select ECUs/PIDs"), P_NORMAL + 1),
        hint_item(key_hint("session", "label" if recording else "save"), P_LOW + 2),
        hint_item(key_hint("pause"), P_LOW),
    ]
    return items
