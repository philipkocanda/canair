"""The ``canair sniff`` table: one row per arbitration ID, changed bytes highlighted."""

from __future__ import annotations

from canair.render.formatting import _HIGHLIGHT_STYLE

#: A byte that changed since the previous frame of its ID: the monitor's and the stepper's
#: changed-byte style, so one encoding means "changed" in every view.
CHANGED_BYTE_STYLE = _HIGHLIGHT_STYLE["green"]

#: What the sniff table's styles mean, for the help legend: (sample, style, meaning).
SNIFF_LEGEND: tuple[tuple[str, str, str], ...] = (
    ("7E4", "cyan", "arbitration ID"),
    ("3C", CHANGED_BYTE_STYLE, "byte changed since the previous frame of that ID"),
)


def render_sniff_table(rows: list[dict]):
    """Render sniff rows as a Rich Table (changed bytes highlighted)."""
    from rich.table import Table
    from rich.text import Text

    table = Table(expand=False, pad_edge=False)
    table.add_column("ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("cnt", justify="right", style="dim")
    table.add_column("Hz", justify="right")
    table.add_column("data (changed bytes highlighted)")

    for r in rows:
        width = 8 if r["extended"] else 3
        id_str = f"{r['id']:0{width}X}"
        data = Text()
        changed = r["changed"]
        for i, b in enumerate(r["data"]):
            volatile = i < len(changed) and changed[i]
            data.append(f"{b:02X}", style=CHANGED_BYTE_STYLE if volatile else "")
            data.append(" ")
        table.add_row(id_str, str(r["count"]), f"{r['hz']:.1f}", data)
    return table
