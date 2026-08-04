"""IOControl TUI — display rendering.

Builds the interactive IOControl TUI screen (an ANSI string) from the
controller's state. Kept out of :mod:`canlib.modes.iocontrol` (which owns the
CAN actuation, key loop, and ecus/ edits) so the ~190-line renderer is its own
concern. ``render_iocontrol`` takes the ``_IOControlTUI`` instance and reads its
state; it also clamps the scroll viewport (``tui._scroll_top``) so the cursor
stays visible — a presentation concern that belongs here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..tui import terminal_columns as _terminal_columns
from ..tui import terminal_lines as _terminal_lines
from ._iocontrol_actuate import ACTUATOR_ERROR, ACTUATOR_OFF, ACTUATOR_ON

if TYPE_CHECKING:
    from .iocontrol import _IOControlTUI


def _truncate_text(text: str, width: int) -> str:
    """Truncate text to visible width using ASCII ellipsis."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def render_iocontrol(tui: _IOControlTUI) -> str:
    """Build the IOControl TUI display as a plain string with ANSI codes."""
    lines = []
    lines.append(f"\033[1;36m  IOControl TUI — {tui.ecu_key} (0x{tui.tx_id:03X})\033[0m")
    sess = "\033[32mactive\033[0m" if tui._session_active else "\033[2mnot started\033[0m"
    poll = "  \033[2m[polling]\033[0m" if tui._status_polling else ""
    # View-mode indicator: curated / all / discoveries, colour-coded.
    curated_n = sum(1 for c in tui.all_cmds.values() if not c.get("discovery"))
    disc_n = sum(1 for c in tui.all_cmds.values() if c.get("discovery"))
    if tui.view_mode == "curated":
        mode_lbl = f"\033[36mcurated\033[0m ({curated_n})"
    elif tui.view_mode == "discoveries":
        mode_lbl = f"\033[33mdiscoveries\033[0m ({disc_n} to triage)"
    else:
        mode_lbl = f"\033[1mall\033[0m ({curated_n}+{disc_n})"
    lines.append(f"\033[2m  Session: \033[0m{sess}{poll}   \033[2mView: \033[0m{mode_lbl}")
    lines.append("")

    # Fixed column widths (ANSI codes don't count toward padding — we pad
    # the *text* content to the desired width, then wrap in colour codes).
    term_w = _terminal_columns()
    did_w = max((len(d) for d in tui.dids), default=4)
    raw_label_w = max((len(tui.cmds[d]["label"]) for d in tui.dids), default=5)

    # "Cmd" column: what we sent (ON / OFF / idle) — always 3 visible chars
    cmd_hdr = "Cmd"
    cmd_hdr_w = len(cmd_hdr)  # 3

    # "Status" column: trailing bytes of the last 0x2F response for this
    # DID (controlStatusRecord). Populated by background 2F{DID}00 polling
    # and by every ON/OFF send. Empty = no tail bytes, None = never polled.
    status_hdr = "Status"
    status_vals_w = max(
        (len(tui.status_bytes[d] or "") for d in tui.dids),
        default=len(status_hdr),
    )
    # Width budget: prefix(5) + did + 2 + label + 2 + 3 + 2 + status + 2 + 13
    # Non-flex components = 5 + did_w + 2 + 2 + 3 + 2 + 2 + 13 = 29 + did_w
    non_flex = 29 + did_w
    # Reserve at least 5 chars for status_w, then give label what's left;
    # if we still don't fit, shrink label first (down to a floor of 8),
    # then shrink status to fit the terminal.
    min_status_w = max(len(status_hdr), 5)
    avail = max(0, term_w - non_flex - min_status_w)
    label_w = min(raw_label_w, max(8, avail)) if avail else max(8, raw_label_w)
    status_w = min(
        max(status_vals_w, len(status_hdr)), 40, max(min_status_w, term_w - non_flex - label_w)
    )

    # Separator widths for the ruler. The header line starts at column 0
    # (5-space prefix is part of its content); the ruler line is inset by
    # 2 spaces as visual scaffolding, so shrink it by 2 to match width.
    total_w = 5 + did_w + 2 + label_w + 2 + cmd_hdr_w + 2 + status_w + 2 + 13
    ruler = "─" * max(1, total_w - 2)

    # Header row — plain text, dim
    # Layout: 3 (prefix) + 2 (verified mark) + did_w + 2 + label_w + 2 + cmd_hdr_w + 2 + status_w + 2 + …
    hdr = (
        f"     "  # 3 (prefix) + 2 (verified)
        f"{'DID':<{did_w}}  "
        f"{'Label':<{label_w}}  "
        f"{cmd_hdr:<{cmd_hdr_w}}  "
        f"{status_hdr:<{status_w}}  "
        f"Last response"
    )
    lines.append(f"\033[2m{hdr}\033[0m")
    lines.append(f"\033[2m  {ruler}\033[0m")

    # Viewport: chrome above (3 banner + 2 column header = 5 lines) and
    # below (1 scroll-indicator + 1 status/input + 1 key-hint = 3 lines)
    # leaves N rows for DIDs. Clamp scroll_top so the cursor is visible.
    term_h = _terminal_lines()
    chrome = 5 + 3
    viewport_rows = max(5, term_h - chrome)
    n_dids = len(tui.dids)
    # Clamp scroll_top into range and make sure the cursor is on-screen.
    if tui.cursor < tui._scroll_top:
        tui._scroll_top = tui.cursor
    elif tui.cursor >= tui._scroll_top + viewport_rows:
        tui._scroll_top = tui.cursor - viewport_rows + 1
    tui._scroll_top = max(0, min(tui._scroll_top, max(0, n_dids - viewport_rows)))
    visible_start = tui._scroll_top
    visible_end = min(n_dids, tui._scroll_top + viewport_rows)

    for i in range(visible_start, visible_end):
        did = tui.dids[i]
        cmd = tui.cmds[did]
        is_cursor = i == tui.cursor
        state = tui.state[did]
        resp = tui.last_response.get(did, "")
        sb = tui.status_bytes.get(did)

        # Cursor indicator (3 chars: " ▸ " or "   ")
        prefix = " \033[1m▸\033[0m " if is_cursor else "   "

        # Verified / discovery marker (1 char + space = 2):
        #   ✓  — curated, verified
        #   ?  — curated, unverified
        #   »  — scanner-discovered (not yet promoted)
        if cmd.get("discovery"):
            v_mark = "\033[35m»\033[0m "
        elif cmd["verified"]:
            v_mark = "\033[32m✓\033[0m "
        else:
            v_mark = "\033[33m?\033[0m "

        # DID + Label (bold if cursor)
        b0 = "\033[1m" if is_cursor else ""
        b1 = "\033[0m" if is_cursor else ""
        label_text = _truncate_text(cmd["label"], label_w)
        did_label = f"{b0}{did:<{did_w}}  {label_text:<{label_w}}{b1}"

        # "Cmd" column — what we last sent (3 visible chars)
        if state == ACTUATOR_ON:
            cmd_part = "\033[1;32mON \033[0m"
        elif state == ACTUATOR_OFF:
            cmd_part = "\033[2mOFF\033[0m"
        elif state == ACTUATOR_ERROR:
            cmd_part = "\033[1;31mERR\033[0m"
        else:
            cmd_part = "\033[2m · \033[0m"

        # "Status" column — trailing bytes of the last 0x2F response.
        # None = never observed (waiting for first poll). "" = positive
        # response with no tail bytes (3-byte 6F{DID} echo only).
        if sb is None:
            text = f"{'—':<{status_w}}"
            status_part = f"\033[2m{text}\033[0m"
        elif sb == "":
            text = f"{'·':<{status_w}}"
            status_part = f"\033[2m{text}\033[0m"
        elif sb.startswith("ERR") or sb.startswith("NRC"):
            text = f"{_truncate_text(sb, status_w):<{status_w}}"
            status_part = f"\033[31m{text}\033[0m"
        else:
            text = f"{_truncate_text(sb, status_w):<{status_w}}"
            status_part = f"\033[36m{text}\033[0m"

        # Response column — raw hex, dimmed
        resp_part = f"  \033[2m{resp}\033[0m" if resp else ""

        lines.append(f"{prefix}{v_mark}{did_label}  {cmd_part}  {status_part}{resp_part}")

    # Scroll indicator: show position + ↑/↓ hints when list overflows.
    if n_dids > viewport_rows:
        above = visible_start
        below = n_dids - visible_end
        up = "\033[33m↑\033[0m" if above else "\033[2m·\033[0m"
        dn = "\033[33m↓\033[0m" if below else "\033[2m·\033[0m"
        lines.append(
            f"\033[2m  {up}{dn} {tui.cursor + 1}/{n_dids} "
            f"(viewing {visible_start + 1}–{visible_end}, {above}↑ {below}↓)\033[0m"
        )
    else:
        lines.append("")
    if tui._hex_input is not None:
        did = tui.dids[tui.cursor]
        lines.append(f"  \033[1;33mValue for {did} (hex): \033[0m{tui._hex_input}\033[5m▏\033[0m")
        lines.append(
            "\033[2m  Type hex bytes, Enter to send 2F{DID}03{value}, Esc to cancel\033[0m"
        )
    elif tui._edit_input is not None:
        did = tui.dids[tui.cursor]
        field, buf = tui._edit_input
        if field == "promote":
            lines.append(
                f"  \033[1;33mLabel for new curated entry from {did}: \033[0m{buf}\033[5m▏\033[0m"
            )
            lines.append(
                "\033[2m  on/off inferred from response length; Enter to save, Esc to cancel\033[0m"
            )
        else:
            lines.append(f"  \033[1;33m{field.capitalize()} for {did}: \033[0m{buf}\033[5m▏\033[0m")
            lines.append(
                "\033[2m  Type new value, Enter to save to ecus/*.yaml, Esc to cancel\033[0m"
            )
    elif tui._status:
        lines.append(f"  {tui._status}")
    # Show last value hint for +/- keys
    did = tui.dids[tui.cursor]
    val_hint = ""
    if did in tui.last_value:
        val_hint = f"  \033[2mlast value: {tui.last_value[did].hex().upper()}\033[0m"
    lines.append(
        f"\033[2m  ↑↓/jk Nav  PgUp/Dn  g/G Top/Bot  Enter Toggle  o OFF  v Value  +/- Step  "
        f"e Label  n Notes  m Verified  d View  P Promote  q Quit\033[0m{val_hint}"
    )
    return "\n".join(lines)
