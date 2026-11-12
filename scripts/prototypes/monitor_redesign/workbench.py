"""Direction C — "Workbench": panes for PIDs, values and the selected signal; a function-key bar.

An instrument cluster on top, three panes, and Midnight Commander's key bar at the bottom::

    LINK             POLL                RECORD                  CAR
    ✓ home 9ms       LIVE #412 · 2.0s    ● REC 04:12 · 1,204     CHARGING · BMS 81 standard
    ╭ PIDs ───────╮╭ Values ─────────────────────────────╮╭ Signal ─────────────╮
    │▼ BMS        ││ (values)                            ││ SOC_BMS   85.50 %   │
    │  [x] 2101 42││                                     ││ ✓ verified [x] ship │
    │  [ ] 2102 32││                                     ││ ▁▂▃▅▆▇ (history)    │
    ╰─────────────╯╰─────────────────────────────────────╯╰─────────────────────╯
     1Help  2Pick  3View  4Edit  5Verify  6Ship  7Label  8New  9Menu  10Quit

* The PIDs pane replaces the picker modal: ``enter`` on a PID polls it or stops polling it,
  while the values keep updating beside it.
* The Signal pane (the inspector) shows the selected signal's definition, the range seen this
  run and a sparkline of its history, which the monitor collects and does not show today.
* The cluster gives each concern a labelled place, so a fault reads as "LINK went red".
* The key bar is the toolbar: ten fixed slots, clickable, ``F1``–``F10``. ``F9`` opens a menu
  bar over the top line with every command. The letter keys keep working.
* Narrow: one pane at a time with a tab strip (``tab`` switches), the cluster on two lines
  without labels, the key bar shortens its words to fit ten slots.
"""

from __future__ import annotations

from typing import ClassVar

from chrome import (
    NARROW,
    MockupApp,
    Part,
    ValuesView,
    car_parts,
    fit,
    link_parts,
    poll_parts,
    record_parts,
)
from fake import PAYLOADS
from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Sparkline, Static, Tree

from canair.render.glyphs import OK

#: (F-key number, label, command id or a workbench action)
FKEYS = [
    (1, "Help", "help"),
    (2, "Pick", "@pids"),
    (3, "View", "view_next"),
    (4, "Edit", "edit"),
    (5, "Verify", "verify"),
    (6, "Ship", "ship"),
    (7, "Label", "label"),
    (8, "New", "new"),
    (9, "Menu", "@menu"),
    (10, "Quit", "quit"),
]
PANES = ("pids", "values", "inspector")
PANE_TITLES = {"pids": "PIDs", "values": "Values", "inspector": "Signal"}


class FKey(Static):
    DEFAULT_CSS = "FKey { width: auto; } FKey:hover { text-style: bold; }"

    def __init__(self, num: int, label: str, target: str) -> None:
        super().__init__("")
        self.num, self.label, self.target = num, label, target

    def fit_to(self, slot: int) -> None:
        num = str(self.num)
        label = self.label[: max(1, slot - len(num) - 1)]
        self.update(
            f"[b]{num}[/][reverse $secondary]{escape(label.ljust(slot - len(num) - 1))}[/] "
        )

    def on_click(self) -> None:
        self.app.run_fkey(self.target)  # type: ignore[attr-defined]


class Gauge(Static):
    """One cluster zone: a dim label line over its value line."""

    DEFAULT_CSS = "Gauge { width: 1fr; height: auto; padding: 0 1; text-wrap: nowrap; }"


class WorkbenchApp(MockupApp):
    CSS = """
    Screen { layout: vertical; background: transparent; }
    #cluster { height: auto; border-bottom: solid $primary; }
    #tabs { height: 1; display: none; padding: 0 1; }
    #panes { height: 1fr; }
    #panes > * { border: round $primary; border-title-color: $text-muted; }
    #panes > *:focus, #panes > *:focus-within { border: round $accent; }
    #panes > *:focus { border-title-color: $text; }
    #pids { width: 26; background: transparent; }
    #values { width: 1fr; }
    #inspector { width: 36; }
    #inspector Static { height: auto; }
    #spark { height: 3; margin: 1 0; }
    #fkeys { height: 1; }
    .-narrow #tabs { display: block; }
    .-narrow #panes > * { display: none; width: 1fr; }
    .-narrow #panes > .-shown { display: block; }
    .-mid #inspector { display: none; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        *(Binding(f"f{n}", f"fkey('{target}')", show=False) for n, _, target in FKEYS),
        Binding("tab", "pane(1)", show=False, priority=True),
        Binding("shift+tab", "pane(-1)", show=False, priority=True),
    ]

    pane = "values"

    def compose(self) -> ComposeResult:
        with Horizontal(id="cluster"):
            for zone in ("link", "poll", "record", "car"):
                yield Gauge("", id=f"g-{zone}")
        yield Static("", id="tabs")
        with Horizontal(id="panes"):
            tree: Tree[tuple[str, str]] = Tree("ECUs", id="pids")
            tree.show_root = False
            yield tree
            yield ValuesView(id="values")
            with VerticalScroll(id="inspector"):
                yield Static("", id="insp-head")
                yield Sparkline([], id="spark")
                yield Static("", id="insp-body")
        with Horizontal(id="fkeys"):
            for num, label, target in FKEYS:
                yield FKey(num, label, target)

    def on_mount(self) -> None:
        for pane in PANES:
            self.base.query_one(f"#{pane}").border_title = PANE_TITLES[pane]
        self.fill_tree()
        super().on_mount()
        self.base.query_one("#values").focus()

    # -- the PIDs pane ------------------------------------------------------------------
    def fill_tree(self) -> None:
        tree = self.base.query_one(Tree)
        tree.clear()
        for ecu, pids in self.m.tree().items():
            node = tree.root.add(f"[b cyan]{ecu}[/]", expand=ecu in ("BMS", "VCU"))
            for pid, count in pids:
                node.add_leaf(self.pid_label(ecu, pid, count), data=(ecu, pid))

    def pid_label(self, ecu: str, pid: str, count: int) -> str:
        on = (ecu, pid) in self.m.polled
        mark = f"[b]{escape('[x]')}[/]" if on else f"[dim]{escape('[ ]')}[/]"
        fresh = " [green]○[/]" if on else "  "
        return f"{mark} [cyan]{pid}[/]{fresh} [dim]{count}[/]"

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data is None:
            return
        key = event.node.data
        if key not in PAYLOADS:
            self.flash.show("Mockup: no recorded payload for this PID", "warning", secs=3)
        elif key in self.m.polled:
            self.m.polled.remove(key)
            self.flash.show(f"Stopped polling {key[0]} {key[1]}", "info", secs=3)
        else:
            self.m.polled.append(key)
            self.flash.show(f"Polling {key[0]} {key[1]}", "info", secs=3)
        count = len(self.m.pid_def(*key).get("parameters") or {})
        event.node.set_label(self.pid_label(*key, count))
        self.refresh_view()

    # -- keys -----------------------------------------------------------------------------
    def action_fkey(self, target: str) -> None:
        self.run_fkey(target)

    def run_fkey(self, target: str) -> None:
        if target == "@menu":
            self.open_menu("Record", bar=True)
        elif target == "@pids":
            self.show_pane("pids")
        else:
            self.action_cmd(target)

    def action_pane(self, delta: int) -> None:
        mid = self.base.has_class("-mid")
        shown = [p for p in PANES if not (mid and p == "inspector")]
        i = shown.index(self.pane) if self.pane in shown else 0
        self.show_pane(shown[(i + delta) % len(shown)])

    def show_pane(self, pane: str) -> None:
        self.pane = pane
        self.refresh_view()
        self.base.query_one(f"#{pane}").focus()

    def apply_width(self, width: int) -> None:
        super().apply_width(width)
        self.base.set_class(NARROW <= width < 110, "-mid")

    # -- painting -------------------------------------------------------------------------
    def refresh_view(self) -> None:
        if not self.is_mounted:
            return
        m = self.m
        self.base.query_one(ValuesView).show(m)
        self.base.query_one("#values").border_title = (
            f"Values · {escape(m.label)}" if not self.narrow else "Values"
        )
        for pane in PANES:
            self.base.query_one(f"#{pane}").set_class(pane == self.pane, "-shown")
        self.paint_cluster()
        self.paint_tabs()
        self.paint_inspector()
        slot = max(4, self.size.width // len(FKEYS))
        for key in self.base.query(FKey):
            key.fit_to(slot)

    def paint_cluster(self) -> None:
        m = self.m
        zones = {
            "link": ("LINK", link_parts(m, word=False)),
            "poll": ("POLL", poll_parts(m)),
            "record": ("RECORD", record_parts(m)),
            "car": ("CAR", car_parts(m)),
        }
        width = max(10, self.size.width // 4 - 2)
        for zone, (label, parts) in zones.items():
            if self.narrow:
                kept = fit(parts, max(10, self.size.width // 2 - 3), " ")
                text = " ".join(p.markup for p in kept)
            else:
                kept = fit(parts, width, " ")
                text = f"[dim b]{label}[/]\n" + " ".join(p.markup for p in kept)
            self.base.query_one(f"#g-{zone}", Gauge).update(text)
        if self.narrow:
            self.base.query_one("#cluster").styles.layout = "grid"
            self.base.query_one("#cluster").styles.grid_size_columns = 2
        else:
            self.base.query_one("#cluster").styles.layout = "horizontal"
        # A message is the values pane's bottom-border caption, so no bar changes for one.
        msg = self.flash.item()
        self.base.query_one("#values").border_subtitle = msg.markup if msg is not None else ""

    def paint_tabs(self) -> None:
        tabs = []
        for pane in PANES:
            title = PANE_TITLES[pane]
            tabs.append(f"[reverse] {title} [/]" if pane == self.pane else f" [dim]{title}[/] ")
        self.base.query_one("#tabs", Static).update(" ".join(tabs) + "   [dim]tab switches[/]")

    def paint_inspector(self) -> None:
        d = self.m.selected_detail()
        head = self.base.query_one("#insp-head", Static)
        body = self.base.query_one("#insp-body", Static)
        spark = self.base.query_one("#spark", Sparkline)
        if d is None:
            head.update("[dim]Select a signal in the values pane (↑/↓)[/]")
            body.update("")
            spark.display = False
            return
        mark = f"[green]{OK} verified[/]" if d["verified"] else "[yellow]? unverified[/]"
        ship = f"[b]{escape('[x]')}[/] ship" if d["ship"] else f"{escape('[ ]')} ship"
        head.update(
            f"[b]{escape(d['name'])}[/]\n[b]{d['value']}[/] {escape(d['unit'])}\n{mark}  {ship}"
        )
        spark.display = bool(d["history"])
        spark.data = d["history"]
        lo, hi = d["min"], d["max"]
        seen = f"{lo:g} – {hi:g} {escape(d['unit'])}" if lo is not None else "—"
        rows = [
            Part(f"[dim]PID[/]   [cyan]{d['ecu']}[/] [cyan]{d['pid']}[/]"),
            Part(f"[dim]bytes[/] {' '.join(d['bytes']) or '—'}"),
            Part(f"[dim]expr[/]  {escape(d['expression'])}"),
            Part(f"[dim]seen[/]  {seen}"),
        ]
        notes = f"\n[dim]{escape(d['notes'])}[/]" if d["notes"] else ""
        keys = "\n\n[dim]e edit · v verify · x ship[/]"
        body.update("\n".join(r.markup for r in rows) + notes + keys)
