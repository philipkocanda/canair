"""The ``canair sniff`` table: one row per arbitration ID, changed bytes highlighted."""

from __future__ import annotations


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
            data.append(f"{b:02X}", style="bold yellow" if volatile else "white")
            data.append(" ")
        table.add_row(id_str, str(r["count"]), f"{r['hz']:.1f}", data)
    return table
