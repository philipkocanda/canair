"""The signal tables: decoded signals, or each signal's value span, as aligned rows.

``render_param_table`` draws a PID's decoded signals (name, value, verified mark, and optionally
the bytes each reads); ``render_param_ranges`` draws the span each signal took over a run. The
monitor, ``captures --diff`` and the stepper draw through them, and ``print_decoded_params`` and
``print_ecu_results`` print a read's results with them.
"""

from rich.markup import escape
from rich.text import Text

from canair.expression.decoding import ParamRow
from canair.expression.notation import ByteDisplay
from canair.expression.results import ResultEntry
from canair.render import ansi, glyphs
from canair.render.console import console
from canair.render.formatting import format_value, param_byte_index_str, render_hex_line

#: The narrowest a shortened name column gets: enough to tell most signals apart.
_MIN_NAME = 8


def _name_width(max_name: int, fixed: int, width: int | None) -> int:
    """The name column's width: the widest name, or less when the row must fit ``width``.

    ``fixed`` is every other column of the row. The name gives way first, down to
    :data:`_MIN_NAME`, so the value and the verified mark stay on the line on a phone.
    """
    if width is None or max_name + fixed <= width:
        return max_name
    return max(_MIN_NAME, width - fixed)


def _fit_name(name: str, width: int) -> str:
    return name if len(name) <= width else name[: width - 1] + glyphs.ELLIPSIS


def render_param_table(
    params: list[ParamRow],
    *,
    verbose: bool = False,
    indent: str = "      ",
    display: ByteDisplay | None = None,
    selected_name: str | None = None,
    changed_styles: dict[str, str] | None = None,
    width: int | None = None,
) -> Text:
    """Render decoded signal rows as an aligned Rich Text block.

    Each row is ``(name, value, unit, expression, error, verified[, display])``.
    Layout: ``{indent}{name}  {value}  ✓|?`` with columns aligned; ``verbose``
    appends the dimmed expression. Error rows show ``✗ <msg>`` in red.

    When ``display`` (a :class:`~canair.expression.notation.ByteDisplay`) is given, a dimmed
    byte-reference column is appended after the mark, naming which payload
    byte(s) each signal reads in the *same* notation as the ruler drawn above the
    hex (e.g. ``B18-B19``). Returns an empty ``Text`` when there are no signals.

    When ``selected_name`` matches a row's signal name, that row is marked with a
    ``▶`` cursor and reverse-styled — used by the live monitor to show the signal
    currently targeted for in-place editing.

    ``changed_styles`` (``{name: style}``, from :func:`changed_param_highlights`)
    backgrounds a signal's name/value cells with the highlight variant of the
    changed byte it decodes, linking a byte flash to the value it produced. The
    ``▶`` selection style takes precedence when a row is both selected and changed.

    ``width`` is the space the table has (the monitor's pane). When the rows are wider, the
    names are shortened (``…``) so the rows fit; without it the names keep their full length.
    """
    changed_styles = changed_styles or {}
    t = Text()
    if not params:
        return t

    max_val = max(
        len(format_value(r[1], r[2], r[6] if len(r) > 6 else "") if r[1] is not None else "")
        for r in params
    )

    # Precompute byte-reference strings (and their column width) when requested.
    byte_strs: dict[int, str] = {}
    max_bytes_w = 0
    if display is not None:
        for idx, row in enumerate(params):
            byte_strs[idx] = param_byte_index_str(row[3], display)
        max_bytes_w = max((len(s) for s in byte_strs.values()), default=0)

    bytes_w = 2 + max_bytes_w if display is not None else 0
    fixed = len(indent) + 2 + max_val + 2 + 1 + bytes_w
    max_name = _name_width(max(len(r[0]) for r in params), fixed, width)

    for idx, row in enumerate(params):
        name, value, unit, expression, perr, verified = row[:6]
        disp_expr = row[6] if len(row) > 6 else ""
        shown = _fit_name(name, max_name)
        mark_style = "green" if verified else "yellow"
        mark_char = glyphs.OK if verified else "?"
        is_sel = selected_name is not None and name == selected_name
        # Replace the last two indent columns with a "▶ " cursor so the selected
        # row's value/mark columns stay aligned with its neighbours.
        name_prefix = (indent[:-2] + "▶ ") if is_sel and len(indent) >= 2 else indent
        # Selection (reverse) wins over a change-highlight background on the same
        # row — the cursor is the interactive focus and must stay legible.
        changed_style = changed_styles.get(name, "")
        name_style = "reverse bold" if is_sel else changed_style
        if perr or value is None:
            t.append(f"{name_prefix}{shown:<{max_name}}  ", style=name_style)
            t.append(f"{glyphs.FAIL} {perr or 'no value'}\n", style="red")
            continue

        val_str = format_value(value, unit, disp_expr)
        t.append(f"{name_prefix}{shown:<{max_name}}  ", style=name_style)
        t.append(f"{val_str:<{max_val}}  ", style=name_style)
        t.append(mark_char, style=mark_style)
        if display is not None:
            byte_str = byte_strs.get(idx, "")
            pad = max_bytes_w if verbose else 0
            t.append(f"  {byte_str:<{pad}}", style="dim")
        if verbose:
            t.append(f"  {expression}\n", style="dim")
        else:
            t.append("\n")
    return t


def _format_range_value(stat: dict) -> str:
    """One-line value-span string for a :class:`~canair.live.monitor.stats.SignalStat`.

    A numeric signal renders as ``lo – hi unit`` (or a single value when it never
    moved); a non-numeric signal lists its distinct labels (with a ``+N more``
    tail past what was kept); an unsampled signal renders ``—``.
    """
    unit = stat.get("unit", "") or ""
    lo, hi = stat.get("min"), stat.get("max")
    if lo is not None and hi is not None:
        if lo == hi:
            return format_value(lo, unit)
        return f"{format_value(lo, '')} – {format_value(hi, '')} {unit}".strip()
    values = stat.get("values") or []
    if values:
        shown = values[:6]
        hidden = (len(values) - len(shown)) + int(stat.get("overflow", 0))
        text = ", ".join(shown)
        if hidden:
            text += f" (+{hidden} more)"
        return text
    return "—"


def render_param_ranges(
    stats: dict,
    *,
    indent: str = "      ",
    selected_name: str | None = None,
    width: int | None = None,
) -> Text:
    """Render each signal's captured value *span* as an aligned Rich Text block.

    ``stats`` maps ``{param_name: SignalStat}`` (from
    :class:`canair.live.monitor.stats.ParamStats`). Each row is
    ``{indent}{name}  {range}  ✓|?  n=N`` — the numeric min–max (or distinct
    labels) accumulated across the run, the way ``canair investigate``/``decode``
    report a signal's range. ``selected_name`` marks the in-place-edit cursor row
    with ``▶``. Returns an empty ``Text`` when there are no stats. ``width`` shortens the names
    to fit, as in :func:`render_param_table`.
    """
    t = Text()
    if not stats:
        return t
    names = list(stats)
    range_strs = {n: _format_range_value(stats[n]) for n in names}
    max_val = max(len(v) for v in range_strs.values())
    max_n = max(len(f"  n={stats[n].get('n', 0)}") for n in names)
    max_name = _name_width(
        max(len(n) for n in names), len(indent) + 2 + max_val + 2 + 1 + max_n, width
    )

    for name in names:
        stat = stats[name]
        verified = bool(stat.get("verified"))
        mark_style = "green" if verified else "yellow"
        mark_char = glyphs.OK if verified else "?"
        is_sel = selected_name is not None and name == selected_name
        name_prefix = (indent[:-2] + "▶ ") if is_sel and len(indent) >= 2 else indent
        name_style = "reverse bold" if is_sel else ""
        t.append(f"{name_prefix}{_fit_name(name, max_name):<{max_name}}  ", style=name_style)
        t.append(f"{range_strs[name]:<{max_val}}  ", style=name_style)
        t.append(mark_char, style=mark_style)
        t.append(f"  n={stat.get('n', 0)}\n", style="dim")
    return t


def print_decoded_params(params_results: list, verbose: bool = False):
    """Print decoded signal values in a compact aligned table (``✓``/``?``/``✗`` marks).

    Args:
        params_results: list of (name, value, unit, expression, error, verified[, display])
    """
    if not params_results:
        print("  No signals to display")
        return

    max_name = max(len(r[0]) for r in params_results)
    max_val = max(
        len(format_value(r[1], r[2], r[6] if len(r) > 6 else "") if r[1] is not None else "")
        for r in params_results
    )

    for row in params_results:
        name, value, unit, expression, error, verified = row[:6]
        display = row[6] if len(row) > 6 else ""
        mark = ansi.c(glyphs.OK, ansi.raw.GREEN) if verified else ansi.c("?", ansi.raw.YELLOW)
        if error:
            print(f"  {mark} {name:<{max_name}}  {ansi.c(f'{glyphs.FAIL} {error}', ansi.raw.RED)}")
        else:
            val_str = format_value(value, unit, display)
            shown = f"{val_str:<{max_val}}  [{expression}]" if verbose else val_str
            print(f"  {mark} {name:<{max_name}}  {shown}")


def print_ecu_results(
    ecu_label: str,
    pid_results: list[ResultEntry],
    verbose: bool = False,
):
    """Print all PID results for an ECU in a grouped, compact layout.

    Args:
        ecu_label:   ECU name + TX ID, e.g. 'BCM (0x7A0)'.
        pid_results: list of dicts, each with:
            pid:      PID code string, e.g. '22C00B'
            params:   list of (name, value, unit, expression, error, verified) — may be empty
            raw_hex:  full response hex string (optional)
            error:    error string if the PID query failed (optional)
            decode:   UDS decode string for raw/unmapped PIDs (optional)
            unmapped: bool — True for PIDs not in YAML
        verbose:     If True, show expressions.
    """
    if not pid_results:
        return

    c = console()

    # ECU header
    c.print(f"\n  [bold cyan]{ecu_label}[/bold cyan]")

    for entry in pid_results:
        pid = entry["pid"]
        error = entry.get("error")
        params = entry.get("params", [])
        raw_hex = entry.get("raw_hex", "")
        decode = entry.get("decode")
        unmapped = entry.get("unmapped", False)

        # PID sub-header
        tag = " [dim](unmapped)[/dim]" if unmapped else ""
        if error:
            c.print(f"    [cyan]{pid}[/cyan]{tag}  [red]{escape(str(error))}[/red]")
            continue

        c.print(f"    [cyan]{pid}[/cyan]{tag}")

        # Decoded parameters — aligned columns (shared renderer).
        if params:
            c.print(render_param_table(params, verbose=verbose), end="")
        elif decode:
            c.print(f"      {decode}")

        # Raw hex line (shared renderer; no prev_raw → no change highlighting).
        if raw_hex:
            line = render_hex_line(raw_hex, params, unmapped, map_bytes=entry.get("map_bytes", ()))
            c.print(line, end="")
