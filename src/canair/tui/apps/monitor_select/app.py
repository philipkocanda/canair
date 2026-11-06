"""Textual ECU → PID picker for ``canair monitor``: which ECUs, then which PIDs.

Two steps in one screen. Step 1 ticks saved ``@groups`` and ECUs; step 2 lists every
PID of the ticked ECUs (pre-ticked with each ECU's default sweep, so accepting
without changes is the same as typing the bare ECU names). ``space`` toggles a row
and ``enter`` moves on, so the quickest path — highlight one ECU, ``enter``,
``enter`` — needs no tick at all. The same screen serves both entry points, so
picking looks and works the same before and during a run:

- :class:`MonitorSelectApp` — a bare ``canair monitor`` runs it before any
  connection is made (it only needs the profile) and exits with
  ``{ecu: [pid, ...]}`` or ``None``.
- :class:`LiveMonitorSelectScreen` — the running monitor's ``p`` key pushes it,
  opened on what is already polled, and dismisses with the new picks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import ClassVar, cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, Label, SelectionList
from textual.widgets.selection_list import Selection

from canair.live.monitor.select import (
    EcuChoice,
    GroupChoice,
    build_choices,
    build_group_choices,
    selectors_from_selection,
)
from canair.profile import Profile
from canair.tui import styles
from canair.tui.help import SCROLL_HELP_ROWS, LegendRow
from canair.tui.keys import DISMISS_DESCRIPTION, bind, key_hint
from canair.tui.modals import KitModal
from canair.tui.status import (
    P_ESSENTIAL,
    P_HIGH,
    P_LOW,
    P_NORMAL,
    StatusBar,
    StatusItem,
    hint_item,
)
from canair.tui.widgets import CheckSelectionList

Picks = dict[str, list[str]]


class MonitorSelectScreen(KitModal["Picks | None"]):
    """Group/ECU → PID checklist; dismisses with the picks, or ``None`` on cancel.

    Step 1 offers the profile's saved ``@groups`` (rows prefixed ``@``) above its
    ECUs. Ticking a group ticks its member ECUs and seeds step 2 with the PIDs it
    names; an ECU ticked on its own gets its default sweep, and when both apply
    the two are combined. ``initial`` opens the screen on an existing selection
    (ECUs ticked, their PIDs carried into step 2 as they are).

    ``enter`` is bound with priority because the list itself would otherwise take
    it as a second toggle key; in the filter it just returns to the list.

    It is a modal so that the keys of an app beneath it (``s``, ``n``, ``space`` of the
    monitor) cannot reach past it.
    """

    HELP_TITLE: ClassVar[str] = "canair monitor picker — keyboard shortcuts"
    #: How ``enter`` is advertised in the help sheet.
    ACCEPT_DESC: ClassVar[str] = "next / start"
    #: What ``enter`` does on step 2, as shown in the status bar.
    FINISH_DESC: ClassVar[str] = "start"
    #: What ``q`` does, as shown in the status bar.
    QUIT_DESC: ClassVar[str] = "quit"
    #: Whether ``escape`` on step 1 cancels. Off at the top level of the
    #: standalone picker, where ``escape`` must never quit.
    BACK_CANCELS: ClassVar[bool] = False

    # Unscoped, id-selected ``CSS`` (screen CSS is otherwise scoped to the
    # screen's descendants, so it could not style the screen itself), so the
    # picker stays opaque over a host app whose CSS makes every ``Screen``
    # transparent — the monitor does, to keep the terminal's own background.
    SCOPED_CSS = False
    CSS = """
    #monitor-select { background: $background; }
    #monitor-select #title { text-style: bold; margin: 1 2 0 2; }
    #monitor-select #filter { margin: 1 2 0 2; }
    #monitor-select #choices { margin: 1 2; height: 1fr; }
    """

    #: Keys the list handles itself, so they are not in ``BINDINGS``.
    HELP_EXTRA_ROWS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("space", "toggle the highlighted row"),
        *SCROLL_HELP_ROWS,
    )
    HELP_LEGEND: ClassVar[tuple[LegendRow, ...]] = (
        ("@name", styles.HEADING, "a saved group of ECUs"),
        ("2 PIDs", styles.MUTED, "what the row holds"),
        ("[status]", styles.WARNING, "a PID that is not swept by default"),
    )

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("quit", "cancel"),
        *bind("help", "help"),
        *bind("back", "back", show=False),
        *bind("move_down", "move_cursor(1)", show=False),
        *bind("move_up", "move_cursor(-1)", show=False),
        *bind("filter", "focus_filter"),
        *bind("select_all", "toggle_all", priority=True),
        *bind("accept", "advance", desc=ACCEPT_DESC, priority=True),
        *bind("apply", "advance", desc=ACCEPT_DESC, show=False),
    ]

    def __init__(
        self,
        choices: list[EcuChoice],
        groups: list[GroupChoice] | None = None,
        *,
        include_static: bool = False,
        initial: Mapping[str, Iterable[str]] | None = None,
    ):
        super().__init__(id="monitor-select")
        self._choices = choices
        self._by_name = {c.name: c for c in choices}
        self._groups = {g.name: g for g in groups or []}
        self._include_static = include_static
        self._step = 1
        seed = {name: set(pids) for name, pids in (initial or {}).items() if name in self._by_name}
        self._ecus: set[str] = {name for name, pids in seed.items() if pids}  # ticked directly
        self._ticked_groups: set[str] = set()
        self._excluded: set[str] = set()  # unticked by hand although a group covers them
        self._pids: dict[str, set[str]] = {name: seed[name] for name in self._ecus}

    def _covered(self) -> set[str]:
        return {ecu for g in self._ticked_groups for ecu in self._groups[g].picks}

    def _effective_ecus(self) -> set[str]:
        return (self._ecus | self._covered()) - self._excluded

    def compose(self) -> ComposeResult:
        yield Label("", id="title")
        yield Input(placeholder="type to filter…", id="filter")
        yield CheckSelectionList(id="choices")
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        self._show_step(1)

    # -- rendering ---------------------------------------------------------
    def _show_step(self, step: int) -> None:
        self._step = step
        field = self.query_one("#filter", Input)
        field.value = ""
        self._render_list()
        self.query_one("#choices", SelectionList).focus()

    def _render_list(self) -> None:
        needle = self.query_one("#filter", Input).value.strip().lower()
        options: list[Selection] = []
        if self._step == 1:
            effective = self._effective_ecus()
            for group in self._groups.values():
                if needle and needle not in f"@{group.name} {group.description}".lower():
                    continue
                label = Text(
                    f"@{group.name:<15}", style=styles.HEADING, no_wrap=True, overflow="ellipsis"
                )
                detail = f"{len(group.picks):>3} ECUs  {group.description}".rstrip()
                label.append(detail, style=styles.MUTED)
                options.append(
                    Selection(label, f"@{group.name}", group.name in self._ticked_groups)
                )
            for ecu in self._choices:
                if needle and needle not in ecu.name.lower():
                    continue
                label = Text(f"{ecu.name:<16}")
                label.append(f"{len(ecu.pids):>3} PIDs   tx 0x{ecu.tx_id:X}", style=styles.MUTED)
                options.append(Selection(label, ecu.name, ecu.name in effective))
        else:
            ecu_width = max(len(n) for n in self._pids)
            for name, picked in self._pids.items():
                ecu = self._by_name[name]
                pid_width = max(len(p.pid) for p in ecu.pids)
                for p in ecu.pids:
                    haystack = f"{name} {p.pid} {p.signals}".lower()
                    if needle and needle not in haystack:
                        continue
                    label = Text(
                        f"{name:<{ecu_width}}  {p.pid:<{pid_width}}  ",
                        no_wrap=True,
                        overflow="ellipsis",
                    )
                    label.append(p.signals or "(undecoded)", style=styles.MUTED)
                    if not p.swept:
                        label.append(f"  [{p.status}]", style=styles.WARNING)
                    options.append(Selection(label, (name, p.pid), p.pid in picked))

        selection = self.query_one("#choices", SelectionList)
        keep = self._highlighted_value()
        selection.clear_options()
        selection.add_options(options)
        if options:
            values = [option.value for option in options]
            selection.highlighted = values.index(keep) if keep in values else 0
        self._update_header()

    def _highlighted_value(self) -> object | None:
        """The value under the cursor, so a re-render can put the cursor back on it."""
        selection = self.query_one("#choices", SelectionList)
        if selection.highlighted is None or not selection.option_count:
            return None
        return selection.get_option_at_index(selection.highlighted).value

    def _update_header(self) -> None:
        toggle = hint_item(key_hint("pause", "toggle"), P_NORMAL)
        filter_ = hint_item(key_hint("filter"), P_NORMAL)
        all_none = hint_item(key_hint("select_all"), P_LOW)
        if self._step == 1:
            picked = len(self._effective_ecus())
            title = f"Select ECUs to monitor — {picked} of {len(self._choices)} selected"
            accept = hint_item(key_hint("accept", "next"), P_HIGH)
            items = [StatusItem("[dim]step 1/2[/]", P_LOW), toggle, filter_, all_none, accept]
            if self._groups:
                items.append(StatusItem("[dim]@rows are saved groups[/]", P_LOW))
        else:
            total = sum(len(p) for p in self._pids.values())
            title = f"Select PIDs to monitor — {total} selected across {len(self._pids)} ECU(s)"
            items = [
                StatusItem("[dim]step 2/2[/]", P_LOW),
                toggle,
                filter_,
                all_none,
                hint_item(key_hint("back"), P_NORMAL),
                hint_item(key_hint("accept", self.FINISH_DESC), P_HIGH),
            ]
        items += [
            hint_item(key_hint("help"), P_ESSENTIAL),
            hint_item(key_hint("quit", self.QUIT_DESC), P_ESSENTIAL),
        ]
        self.query_one("#title", Label).update(title)
        self.query_one("#status", StatusBar).set_lines(items)

    # -- events ------------------------------------------------------------
    def on_input_changed(self, _event: Input.Changed) -> None:
        self._render_list()

    def on_selection_list_selection_toggled(
        self, event: SelectionList.SelectionToggled[object]
    ) -> None:
        self._record(event.selection.value, event.selection.value in event.selection_list.selected)

    def _record(self, value: object, picked: bool) -> None:
        if self._step == 1 and str(value).startswith("@"):
            self._record_group(str(value)[1:], picked)
            self._render_list()
            return
        if self._step == 1:
            name = str(value)
            if picked:
                self._ecus.add(name)
                self._excluded.discard(name)
            else:
                self._ecus.discard(name)
                self._excluded.add(name)
        else:
            name, pid = cast("tuple[str, str]", value)
            (self._pids[name].add if picked else self._pids[name].discard)(pid)
        self._update_header()

    def _record_group(self, name: str, picked: bool) -> None:
        group = self._groups[name]
        if picked:
            self._ticked_groups.add(name)
            self._excluded -= set(group.picks)
            if group.skipped:
                self.notify(
                    f"@{name}: not in this profile, left out: {', '.join(group.skipped)}",
                    severity="warning",
                )
        else:
            self._ticked_groups.discard(name)
        for ecu in group.picks:  # let step 2 re-seed from the new group set
            self._pids.pop(ecu, None)

    def _seed_pids(self, ecu: EcuChoice) -> set[str]:
        seed: set[str] = set()
        covered = False
        for name in self._ticked_groups:
            picks = self._groups[name].picks.get(ecu.name)
            if picks:
                seed.update(picks)
                covered = True
        if ecu.name in self._ecus or not covered:
            seed.update(ecu.default_pids(self._include_static))
        return seed

    # -- actions -----------------------------------------------------------
    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_move_cursor(self, delta: int) -> None:
        """Move the list cursor, also while typing in the filter."""
        selection = self.query_one("#choices", SelectionList)
        (selection.action_cursor_down if delta > 0 else selection.action_cursor_up)()

    async def action_back(self) -> None:
        """Back out one level: clear the filter, then leave step 2, then (live) close."""
        field = self.query_one("#filter", Input)
        selection = self.query_one("#choices", SelectionList)
        if field.value:
            field.value = ""
            selection.focus()
        elif self.focused is field:
            selection.focus()
        elif self._step == 2:
            self._show_step(1)
        elif self.BACK_CANCELS:
            self.dismiss(None)

    def action_toggle_all(self) -> None:
        selection = self.query_one("#choices", SelectionList)
        values = [
            option.value
            for option in selection.options
            if isinstance(option, Selection) and not str(option.value).startswith("@")
        ]
        if not values:
            return
        everything = all(v in selection.selected for v in values)
        for value in values:
            self._record(value, not everything)
        self._render_list()

    def action_advance(self) -> None:
        if self.focused is self.query_one("#filter", Input):
            self.query_one("#choices", SelectionList).focus()
            return
        if self._step == 1:
            effective = self._effective_ecus()
            if not effective:
                # Nothing ticked: take the highlighted row, so one ECU is just enter.
                value = self._highlighted_value()
                if value is not None:
                    self._record(value, True)
                    effective = self._effective_ecus()
            if not effective:
                self.notify("Select at least one ECU or group first", severity="warning")
                return
            self._pids = {
                ecu.name: self._pids[ecu.name] if ecu.name in self._pids else self._seed_pids(ecu)
                for ecu in self._choices
                if ecu.name in effective
            }
            self._show_step(2)
            return
        result = {
            name: [p.pid for p in self._by_name[name].pids if p.pid in picked]
            for name, picked in self._pids.items()
            if picked
        }
        if not result:
            self.notify("Select at least one PID first", severity="warning")
            return
        self.dismiss(result)


class LiveMonitorSelectScreen(MonitorSelectScreen):
    """The picker as the running monitor's ``p`` key opens it.

    ``escape`` backs out to the monitor from step 1 and ``q`` closes the picker
    rather than the monitor; ``ctrl+c`` is left to the monitor, which quits.
    """

    ACCEPT_DESC = "next / apply"
    FINISH_DESC = "apply"
    QUIT_DESC = DISMISS_DESCRIPTION
    BACK_CANCELS = True

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("quit", "cancel", desc=DISMISS_DESCRIPTION),
        *bind("help", "help"),
        *bind("back", "back", show=False),
        *bind("move_down", "move_cursor(1)", show=False),
        *bind("move_up", "move_cursor(-1)", show=False),
        *bind("filter", "focus_filter"),
        *bind("select_all", "toggle_all", priority=True),
        *bind("accept", "advance", desc=ACCEPT_DESC, priority=True),
        *bind("apply", "advance", desc=ACCEPT_DESC, show=False),
    ]


class MonitorSelectApp(App["Picks | None"]):
    """The standalone picker a bare ``canair monitor`` runs before connecting."""

    BINDINGS: ClassVar[list[Binding]] = [*bind("quit_force", "quit", show=False, priority=True)]

    # canair registers no command-palette commands and pins the theme, so the
    # built-in ctrl+p palette only adds an unstyled modal that overflows a narrow
    # terminal. Disable it, matching every other canair TUI.
    ENABLE_COMMAND_PALETTE = False

    def __init__(
        self,
        choices: list[EcuChoice],
        groups: list[GroupChoice] | None = None,
        *,
        include_static: bool = False,
    ):
        super().__init__()
        self.picker = MonitorSelectScreen(choices, groups, include_static=include_static)

    def on_mount(self) -> None:
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
        self.push_screen(self.picker, self.exit)


def pick_selectors(
    pids_data: dict, *, include_static: bool = False, profile: Profile
) -> list[str] | None:
    """Run the interactive picker; return selectors, or ``None`` if cancelled.

    Raises ``ValueError`` when the active profile defines nothing to monitor.
    The caller is responsible for only calling this on an interactive terminal.
    """
    from canair.ecus.ecu_groups import load_groups
    from canair.ecus.pids import build_ecu_index

    choices = build_choices(build_ecu_index(pids_data))
    if not choices:
        raise ValueError("the active profile defines no pollable PIDs, so there is nothing to pick")

    groups = build_group_choices(
        load_groups(profile=profile), choices, include_static=include_static
    )
    picked = MonitorSelectApp(choices, groups, include_static=include_static).run()
    if not picked:
        return None
    return selectors_from_selection(choices, picked, include_static=include_static)
