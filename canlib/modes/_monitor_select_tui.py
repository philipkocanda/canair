"""Textual ECU → PID picker for ``canair monitor``: which ECUs, then which PIDs.

Two steps in one screen. Step 1 ticks saved ``@groups`` and ECUs; step 2 lists every
PID of the ticked ECUs (pre-ticked with each ECU's default sweep, so accepting
without changes is the same as typing the bare ECU names). The same screen serves
both entry points, so picking looks and works the same before and during a run:

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
from textual.screen import Screen
from textual.widgets import Input, Label, SelectionList
from textual.widgets.selection_list import Selection

from canlib.tui.help import SCROLL_HELP_ROWS, HelpModal, bindings_help_rows
from canlib.tui.keys import DISMISS_DESCRIPTION, bind
from canlib.tui.status import P_ESSENTIAL, P_HIGH, P_LOW, P_NORMAL, StatusBar, StatusItem
from canlib.tui.widgets import CheckSelectionList

from .monitor_select import EcuChoice, GroupChoice

Picks = dict[str, list[str]]


class MonitorSelectScreen(Screen["Picks | None"]):
    """Group/ECU → PID checklist; dismisses with the picks, or ``None`` on cancel.

    Step 1 offers the profile's saved ``@groups`` (rows prefixed ``@``) above its
    ECUs. Ticking a group ticks its member ECUs and seeds step 2 with the PIDs it
    names; an ECU ticked on its own gets its default sweep, and when both apply
    the two are combined. ``initial`` opens the screen on an existing selection
    (ECUs ticked, their PIDs carried into step 2 as they are).
    """

    HELP_TITLE: ClassVar[str] = "Select what to monitor"
    #: What ``ctrl+s`` does on step 2, as shown in the status bar.
    FINISH_HINT: ClassVar[str] = "start"
    #: What ``q`` does, as shown in the status bar.
    QUIT_HINT: ClassVar[str] = "quit"
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

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("quit", "cancel"),
        *bind("quit_force", "cancel", show=False, priority=True),
        *bind("help", "help"),
        *bind("back", "back", show=False),
        *bind("filter", "focus_filter"),
        *bind("select_all", "toggle_all", priority=True),
        *bind("apply", "advance"),
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
        yield Input(placeholder="/ to filter…", id="filter")
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
                label = Text(f"@{group.name:<15}", style="bold", no_wrap=True, overflow="ellipsis")
                detail = f"{len(group.picks):>3} ECUs  {group.description}".rstrip()
                label.append(detail, style="dim")
                options.append(
                    Selection(label, f"@{group.name}", group.name in self._ticked_groups)
                )
            for ecu in self._choices:
                if needle and needle not in ecu.name.lower():
                    continue
                label = Text(f"{ecu.name:<16}")
                label.append(f"{len(ecu.pids):>3} PIDs   tx 0x{ecu.tx_id:X}", style="dim")
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
                    label.append(p.signals or "(undecoded)", style="dim")
                    if not p.swept:
                        label.append(f"  [{p.status}]", style="yellow")
                    options.append(Selection(label, (name, p.pid), p.pid in picked))

        selection = self.query_one("#choices", SelectionList)
        keep = selection.highlighted
        selection.clear_options()
        selection.add_options(options)
        if options:
            selection.highlighted = min(keep or 0, len(options) - 1)
        self._update_header()

    def _update_header(self) -> None:
        if self._step == 1:
            picked = len(self._effective_ecus())
            title = f"Select ECUs to monitor — {picked} of {len(self._choices)} selected"
            items = [
                StatusItem("[dim]step 1/2[/]", P_LOW),
                StatusItem("[dim]space toggle[/]", P_NORMAL),
                StatusItem("[dim]/ filter[/]", P_NORMAL),
                StatusItem("[dim]ctrl+a all/none[/]", P_LOW),
                StatusItem("[dim]ctrl+s next[/]", P_HIGH),
            ]
            if self._groups:
                items.append(StatusItem("[dim]@rows are saved groups[/]", P_LOW))
        else:
            total = sum(len(p) for p in self._pids.values())
            title = f"Select PIDs to monitor — {total} selected across {len(self._pids)} ECU(s)"
            items = [
                StatusItem("[dim]step 2/2[/]", P_LOW),
                StatusItem("[dim]space toggle[/]", P_NORMAL),
                StatusItem("[dim]/ filter[/]", P_NORMAL),
                StatusItem("[dim]ctrl+a all/none[/]", P_LOW),
                StatusItem("[dim]esc back[/]", P_NORMAL),
                StatusItem(f"[dim]ctrl+s {self.FINISH_HINT}[/]", P_HIGH),
            ]
        items += [
            StatusItem("[dim]? help[/]", P_ESSENTIAL),
            StatusItem(f"[dim]q {self.QUIT_HINT}[/]", P_ESSENTIAL),
        ]
        self.query_one("#title", Label).update(title)
        self.query_one("#status", StatusBar).set_lines(items)

    # -- events ------------------------------------------------------------
    def on_input_changed(self, _event: Input.Changed) -> None:
        self._render_list()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.query_one("#choices", SelectionList).focus()

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

    def action_help(self) -> None:
        if isinstance(self.app.screen, HelpModal):
            return
        rows = bindings_help_rows(self) + list(SCROLL_HELP_ROWS)
        self.app.push_screen(HelpModal(self.HELP_TITLE, rows))

    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    async def action_back(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one("#choices", SelectionList).focus()
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
        if self._step == 1:
            effective = self._effective_ecus()
            if not effective:
                self.notify("Select at least one ECU or group first.", severity="warning")
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
            self.notify("Select at least one PID first.", severity="warning")
            return
        self.dismiss(result)


class LiveMonitorSelectScreen(MonitorSelectScreen):
    """The picker as the running monitor's ``p`` key opens it.

    ``escape`` backs out to the monitor from step 1 and ``q`` closes the picker
    rather than the monitor; ``ctrl+c`` is left to the monitor, which quits.
    """

    HELP_TITLE = "Select what to monitor (live)"
    FINISH_HINT = "apply"
    QUIT_HINT = "close"
    BACK_CANCELS = True

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("quit", "cancel", desc=DISMISS_DESCRIPTION),
        *bind("help", "help"),
        *bind("back", "back", show=False),
        *bind("filter", "focus_filter"),
        *bind("select_all", "toggle_all", priority=True),
        *bind("apply", "advance"),
    ]


class MonitorSelectApp(App["Picks | None"]):
    """The standalone picker a bare ``canair monitor`` runs before connecting."""

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
