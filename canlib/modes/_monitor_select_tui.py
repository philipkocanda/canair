"""Textual picker shown by a bare ``canair monitor``: which ECUs, then which PIDs.

Two steps in one screen. Step 1 ticks ECUs; step 2 lists every PID of the ticked
ECUs (pre-ticked with each ECU's default sweep, so accepting without changes is
the same as typing the bare ECU names). It runs before any connection is made —
it only needs the profile — and exits with ``{ecu: [pid, ...]}`` or ``None``.
"""

from __future__ import annotations

from typing import ClassVar, cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Input, Label, SelectionList
from textual.widgets.selection_list import Selection

from canlib.tui.help import HelpMixin
from canlib.tui.keys import bind
from canlib.tui.widgets import CheckSelectionList

from .monitor_select import EcuChoice, GroupChoice


class MonitorSelectApp(HelpMixin, App["dict[str, list[str]] | None"]):
    """Group/ECU → PID checklist; ``run()`` returns the picks, or ``None`` on cancel.

    Step 1 offers the profile's saved ``@groups`` (rows prefixed ``@``) above its
    ECUs. Ticking a group ticks its member ECUs and seeds step 2 with the PIDs it
    names; an ECU ticked on its own gets its default sweep, and when both apply
    the two are combined.
    """

    HELP_TITLE = "Select what to monitor"

    CSS = """
    #title { text-style: bold; margin: 1 2 0 2; }
    #hint { color: $text-muted; margin: 0 2; }
    #filter { margin: 1 2 0 2; }
    #choices { margin: 1 2; height: 1fr; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("quit", "quit"),
        *bind("quit_force", "quit", show=False, priority=True),
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
    ):
        super().__init__()
        self._choices = choices
        self._by_name = {c.name: c for c in choices}
        self._groups = {g.name: g for g in groups or []}
        self._include_static = include_static
        self._step = 1
        self._ecus: set[str] = set()  # ticked directly, on an ECU row
        self._ticked_groups: set[str] = set()
        self._excluded: set[str] = set()  # unticked by hand although a group covers them
        self._pids: dict[str, set[str]] = {}

    def _covered(self) -> set[str]:
        return {ecu for g in self._ticked_groups for ecu in self._groups[g].picks}

    def _effective_ecus(self) -> set[str]:
        return (self._ecus | self._covered()) - self._excluded

    def compose(self) -> ComposeResult:
        yield Label("", id="title")
        yield Label("", id="hint")
        yield Input(placeholder="/ to filter…", id="filter")
        yield CheckSelectionList(id="choices")
        yield Footer()

    def on_mount(self) -> None:
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
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
            hint = "step 1/2 · space toggle · ctrl+s next: choose PIDs"
            if self._groups:
                hint += " · @rows are saved groups"
        else:
            total = sum(len(p) for p in self._pids.values())
            title = f"Select PIDs to monitor — {total} selected across {len(self._pids)} ECU(s)"
            hint = "step 2/2 · space toggle · ctrl+s start monitoring · esc back to ECUs"
        self.query_one("#title", Label).update(title)
        self.query_one("#hint", Label).update(hint)

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
    async def action_quit(self) -> None:
        self.exit(None)

    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    async def action_back(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one("#choices", SelectionList).focus()
        elif self._step == 2:
            self._show_step(1)

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
        self.exit(result)
