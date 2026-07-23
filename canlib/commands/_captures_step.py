#!/usr/bin/env python3
"""Interactive step-through TUI for the ``captures`` command.

The full-screen, keyboard-driven viewers behind ``captures --step`` and
``captures --step --pair``: render a single capture (or two, side by side)
with decoded params and a byte-level diff, and walk the matching captures with
arrow keys. The data selection/analysis they build on lives in
:mod:`_captures_query`; this module is purely the rendering + input loop.
"""

import sys
from contextlib import contextmanager
from pathlib import Path

from canlib.align import DEFAULT_JOIN_TOL_S
from canlib.capture_dates import entry_datetime
from canlib.commands._captures_query import (
    _DIM,
    _RESET,
    _YELLOW,
    PidDefs,
    _build_pair_frames,
    _capture_key,
    _dedupe_payloads,
    _gather_query,
    _key_ordinals,
    _prev_same_index,
    load_all_captures,
)
from canlib.states import join_states as _join_states


def _read_key(fd: int) -> str:
    """Read a single keypress (or escape sequence) from a raw/cbreak stdin."""
    from canlib.tui import read_key_raw

    return read_key_raw(fd)


@contextmanager
def _raw_fullscreen(fd: int):
    """Enter cbreak input on the alternate screen buffer, restoring on exit.

    Shared by the single- and pair-step interactive loops: switches ``fd`` to
    cbreak, hides the cursor, and swaps to the alternate screen, then always
    restores the terminal settings, cursor, and primary screen on the way out.
    """
    import termios
    import tty

    old_settings = termios.tcgetattr(fd)
    sys.stdout.write("\033[?1049h\033[?25l")  # alt screen + hide cursor
    sys.stdout.flush()
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\033[?25h\033[?1049l")  # show cursor, leave alt screen
        sys.stdout.flush()


def _render_capture_block(
    console,
    captures: list[dict],
    i: int,
    defs: dict[tuple[str, str], PidDefs],
    prev_idx: list[int | None],
    ordinals: list[tuple[int, int]],
    *,
    rulers: bool = False,
    position: str = "",
) -> None:
    """Render one capture: header, decoded params, optional ruler, byte-diff hex.

    Shared by the single-capture step frame and each half of the two-capture
    pair frame. PID definitions (``parameters``/``tx_id``) are resolved
    per-capture from ``defs`` so one interleaved list can span multiple
    PIDs/ECUs; the byte-diff compares the payload against the previous capture of
    the *same* (ECU, PID) — via ``prev_idx`` — rendered dimmed above. ``position``
    is the free-text locator shown next to the PID (e.g. ``capture 3/50``).
    """
    from rich.markup import escape

    from canlib.decoding import decode_param_rows
    from canlib.formatting import _render_hex_line, render_byte_rulers, render_param_table

    e = captures[i]
    key = _capture_key(e)
    parameters, tx_id = defs.get(key, ({}, None))
    multi = len(defs) > 1

    pj = prev_idx[i]
    prev = captures[pj] if pj is not None else None

    norm = e["payload"].upper().replace(" ", "")
    prev_norm = prev["payload"].upper().replace(" ", "") if prev else ""
    n_bytes = len(norm) // 2

    # Decode the *current* capture (drives the table + byte colours for this frame).
    rows = decode_param_rows(e["payload"], parameters)
    unmapped = not rows

    # Header: ECU / PID + position, timestamp, state, label, file.
    ecu_display = escape(e["ecu"])
    pid_display = escape(str(e["pid"]))
    tx_str = f" (0x{tx_id:03X})" if isinstance(tx_id, int) else ""
    ts = e.get("time") or e.get("date") or ""
    _st = _join_states(e.get("vehicle_states"))
    state = f"  states={escape(_st)}" if _st else ""
    label = f"  [{escape(e['label'])}]" if e.get("label") else ""
    file_str = f"  ({escape(e['file'])})" if e.get("file") else ""

    ord_n, ord_m = ordinals[i]
    per_pid = f" · this PID {ord_n}/{ord_m}" if multi else ""
    pos = f"{escape(position)}{per_pid}".strip()
    pos_str = f"  [dim]{pos}[/dim]" if pos else ""

    console.print(f"\n  [bold cyan]{ecu_display}{tx_str}[/bold cyan]")
    console.print(f"    [yellow]{pid_display}[/yellow]{pos_str}")
    console.print(f"    [bold]{escape(ts)}[/bold][dim]{state}{label}{file_str}[/dim]")

    # Capture note (if any).
    note = (e.get("notes") or "").strip()
    if note:
        console.print(f"    [dim]note:[/dim] {escape(note)}")

    # Decoded-parameter block (aligned columns, verification marks, byte indices).
    if rows:
        console.print(render_param_table(rows, n_bytes=n_bytes), end="")

    # Byte-index ruler (opt-in via --rulers), aligned with the hex byte columns below.
    prev_ts = (prev.get("time") or prev.get("date") or "") if prev else ""
    max_ts = max(len(ts), len(prev_ts))
    if rulers and n_bytes:
        console.print(
            render_byte_rulers(n_bytes, rows, prefix_width=8 + max_ts), end="", soft_wrap=True
        )

    # Previous same-PID capture (dimmed, no highlight) for visual reference, then
    # the current capture with per-byte change highlighting against it.
    if prev is not None:
        prev_prefix = f"      {prev_ts:<{max_ts}}  "
        console.print(
            _render_hex_line(prev_norm, rows, unmapped, prefix=prev_prefix, prefix_style="dim"),
            end="",
            soft_wrap=True,
        )
    prefix = f"    > {ts:<{max_ts}}  "
    console.print(
        _render_hex_line(norm, rows, unmapped, prev_raw=prev_norm, prefix=prefix),
        end="",
        soft_wrap=True,
    )


def _render_step_frame(
    console,
    captures: list[dict],
    i: int,
    defs: dict[tuple[str, str], PidDefs],
    prev_idx: list[int | None],
    ordinals: list[tuple[int, int]],
    status: str = "",
    prompt: str | None = None,
    rulers: bool = False,
) -> None:
    """Render one capture full-screen with the interactive footer/prompt.

    ``prompt`` (when set) replaces the status line with a bold input prompt, used
    by the note-edit and delete-confirm sub-loops.
    """
    from rich.markup import escape

    _render_capture_block(
        console,
        captures,
        i,
        defs,
        prev_idx,
        ordinals,
        rulers=rulers,
        position=f"capture {i + 1}/{len(captures)}",
    )

    # Footer: key hints, then either an input prompt or a transient status.
    console.print(
        "\n  [dim]←/h/p prev   →/l/n/space next   PgUp/PgDn ±100   g/G first/last   "
        ": goto   e note   d delete   q quit[/dim]"
    )
    if prompt is not None:
        console.print(f"  [bold yellow]{escape(prompt)}[/bold yellow]")
    elif status:
        console.print(f"  [yellow]{escape(status)}[/yellow]")


def _render_step_pair_frame(
    console,
    captures: list[dict],
    frames: list[tuple[int | None, int | None]],
    f: int,
    defs: dict[tuple[str, str], PidDefs],
    prev_idx: list[int | None],
    ordinals: list[tuple[int, int]],
    key_a: tuple[str, str],
    key_b: tuple[str, str],
    tol_s: float,
    status: str = "",
    rulers: bool = False,
) -> None:
    """Render one two-capture pair frame: two stacked capture blocks + footer.

    ``frames[f]`` is ``(left_idx, right_idx)`` into ``captures`` (either side may
    be ``None`` when a capture had no counterpart within ``tol_s``). The header
    shows the pairing tolerance and, when both sides are present, the timestamp
    delta between them.
    """
    from rich.markup import escape

    li, ri = frames[f]

    dt_a = entry_datetime(captures[li]) if li is not None else None
    dt_b = entry_datetime(captures[ri]) if ri is not None else None
    dt_str = ""
    if dt_a is not None and dt_b is not None:
        dt_str = f"   Δt={abs((dt_a - dt_b).total_seconds()):.2f}s"

    console.print(f"\n  [bold]pair {f + 1}/{len(frames)}[/bold]  [dim]tol={tol_s:g}s{dt_str}[/dim]")

    def _side(idx: int | None, key: tuple[str, str], position: str) -> None:
        if idx is not None:
            _render_capture_block(
                console, captures, idx, defs, prev_idx, ordinals, rulers=rulers, position=position
            )
        else:
            label = escape(f"{key[0]}:{key[1]}")
            console.print(f"\n  [dim]— no {label} capture within {tol_s:g}s —[/dim]")

    _side(li, key_a, "left")
    console.print("  [dim]" + "─" * 44 + "[/dim]")
    _side(ri, key_b, "right")

    console.print(
        "\n  [dim]←/h/p prev   →/l/n/space next   PgUp/PgDn ±100   g/G first/last   "
        ": goto   q quit[/dim]"
    )
    if status:
        console.print(f"  [yellow]{escape(status)}[/yellow]")


def cmd_step(
    entries: list[dict],
    query,
    show_all: bool = False,
    captures_dir: Path | None = None,
    rulers: bool = False,
) -> None:
    """Interactively step through captures matching ``query``, one at a time.

    ``query`` is a canlib.query selection (``"VCU"``, ``"VCU:2101,2102"``,
    ``"VCU:2101 BMS:2101"``). Captures are interleaved chronologically across the
    selected PIDs; the byte-diff for each frame is computed against the previous
    capture of the same (ECU, PID).

    Arrow keys (or vim ``h``/``l``) move between captures; PgUp/PgDn skip ±100;
    ``:`` jumps to a capture number; ``g``/``G`` go to first/last. ``e``
    edits/adds the current capture's note; ``d`` deletes it (y/N confirm). Both
    mutate the source YAML and reload in place.

    Steps through *unique* payloads (per PID) by default; ``show_all=True`` walks
    every capture. Falls back to ``cmd_diff`` when stdin/stdout is not a TTY.
    """
    from rich.console import Console

    from canlib.captures import delete_capture, set_capture_note

    if captures_dir is None:
        from canlib.profile import active

        captures_dir = active().captures_dir

    def build_list(src: list[dict], warn: bool):
        caps, defs = _gather_query(src, query, warn=warn)
        if not show_all:
            caps = _dedupe_payloads(caps)
        prev_idx = _prev_same_index(caps)
        ordinals = _key_ordinals(caps)
        return caps, defs, prev_idx, ordinals

    captures, defs, prev_idx, ordinals = build_list(entries, warn=True)
    if not captures:
        return

    # Non-interactive (piped) — fall back to the static diff view.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        from canlib.commands.captures import cmd_diff

        print("  (not a TTY — falling back to --diff view)")
        cmd_diff(entries, query, show_all=show_all, rulers=rulers)
        return

    console = Console(highlight=False)
    fd = sys.stdin.fileno()

    i = len(captures) - 1  # start at the most recent capture
    status = ""
    final_msg = ""

    def redraw(prompt: str | None = None) -> None:
        sys.stdout.write("\033[2J\033[H")  # clear + home
        _render_step_frame(
            console,
            captures,
            i,
            defs,
            prev_idx,
            ordinals,
            status=status,
            prompt=prompt,
            rulers=rulers,
        )

    def reload() -> bool:
        """Re-read captures from disk and rebuild the list. False if empty."""
        nonlocal captures, defs, prev_idx, ordinals, i
        fresh = load_all_captures(captures_dir)
        captures, defs, prev_idx, ordinals = build_list(fresh, warn=False)
        if not captures:
            return False
        i = min(i, len(captures) - 1)
        return True

    # Alternate screen buffer + hidden cursor for clean redraws.
    with _raw_fullscreen(fd):
        while True:
            redraw()
            status = ""

            key = _read_key(fd)
            if key in ("q", "Q", "\x1b\x1b", "\x1b", "\x03"):  # q / Esc / Ctrl-C
                break
            elif key in ("\x1b[D", "h", "p"):  # left / prev
                if i > 0:
                    i -= 1
                else:
                    status = "At first capture"
            elif key in ("\x1b[C", "l", "n", " "):  # right / next
                if i < len(captures) - 1:
                    i += 1
                else:
                    status = "At last capture"
            elif key in ("\x1b[H", "g"):  # Home / g — first
                i = 0
            elif key in ("\x1b[F", "G"):  # End / G — last
                i = len(captures) - 1
            elif key in ("\x1b[6~", "]"):  # PageDown / ] — forward 100
                i = min(i + 100, len(captures) - 1)
            elif key in ("\x1b[5~", "["):  # PageUp / [ — back 100
                i = max(i - 100, 0)
            elif key in (":", "#"):  # jump to a specific capture number
                buf = ""
                cancelled = False
                while True:
                    redraw(
                        prompt=f"go to capture # (1-{len(captures)}, Enter=go · Esc=cancel): {buf}\u2588"
                    )
                    k = _read_key(fd)
                    if k in ("\r", "\n"):
                        break
                    if k in ("\x1b", "\x03"):
                        cancelled = True
                        break
                    if k in ("\x7f", "\x08"):  # backspace
                        buf = buf[:-1]
                    elif k.isdigit():
                        buf += k
                if cancelled or not buf:
                    status = "Jump cancelled"
                else:
                    n = int(buf)
                    if 1 <= n <= len(captures):
                        i = n - 1
                    else:
                        i = max(0, min(n - 1, len(captures) - 1))
                        status = f"Clamped to {i + 1} (valid: 1-{len(captures)})"
            elif key in ("e", "E"):  # edit / add note
                cap = captures[i]
                buf = (cap.get("notes") or "").replace("\n", " ").strip()
                cancelled = False
                while True:
                    redraw(prompt=f"note (Enter=save · Esc=cancel): {buf}\u2588")
                    k = _read_key(fd)
                    if k in ("\r", "\n"):
                        break
                    if k in ("\x1b", "\x03"):
                        cancelled = True
                        break
                    if k in ("\x7f", "\x08"):  # backspace
                        buf = buf[:-1]
                    elif len(k) == 1 and k.isprintable():
                        buf += k
                if cancelled:
                    status = "Note edit cancelled"
                else:
                    try:
                        set_capture_note(
                            captures_dir / cap["file"],
                            cap["_session_idx"],
                            cap["_capture_idx"],
                            buf,
                        )
                        saved = "Note saved" if buf.strip() else "Note cleared"
                        if not reload():
                            final_msg = saved + " — no captures left"
                            break
                        status = saved
                    except Exception as ex:
                        status = f"Note save failed: {ex}"
            elif key in ("d", "D"):  # delete current capture (confirmed)
                cap = captures[i]
                redraw(prompt="Delete this capture? (y/N)")
                if _read_key(fd) in ("y", "Y"):
                    try:
                        delete_capture(
                            captures_dir / cap["file"],
                            cap["_session_idx"],
                            cap["_capture_idx"],
                        )
                        if not reload():
                            final_msg = "Capture deleted — no captures left"
                            break
                        status = "Capture deleted"
                    except Exception as ex:
                        status = f"Delete failed: {ex}"
                else:
                    status = "Delete cancelled"

    if final_msg:
        print(f"  {final_msg}")


def cmd_step_pair(
    entries: list[dict],
    query,
    *,
    show_all: bool = False,
    captures_dir: Path | None = None,
    rulers: bool = False,
    tol_s: float = DEFAULT_JOIN_TOL_S,
) -> None:
    """Interactively step through two ECU:PID selections, comparing them side by side.

    ``query`` must resolve to exactly two distinct (ECU, PID) keys (e.g.
    ``"VCU:2101 BMS:2101"``). Captures from the two keys are joined by nearest
    timestamp within ``tol_s`` seconds (the ``--join-tol`` window, following
    :mod:`canlib.align`); each frame stacks the paired captures, with a ``Δt``
    readout. Captures with no counterpart in range are shown alone. Captures
    lacking a usable timestamp are excluded from pairing (and counted).

    Read-only navigation only (arrows/``h``/``l``, PgUp/PgDn ±100, ``g``/``G``,
    ``:`` goto, ``q`` quit); edit/delete a capture from the single-capture
    ``--step`` view. No-op when stdin/stdout is not a TTY.
    """
    from rich.console import Console

    if captures_dir is None:
        from canlib.profile import active

        captures_dir = active().captures_dir

    captures, defs = _gather_query(entries, query, warn=True)
    if not captures:
        return
    if not show_all:
        captures = _dedupe_payloads(captures)

    n_keys = len({_capture_key(e) for e in captures})
    if n_keys != 2:
        print(
            f"  {_YELLOW}--pair needs exactly two distinct ECU:PID selections; "
            f"this query has {n_keys}.{_RESET}"
        )
        print(f'  {_DIM}e.g. canair captures "VCU:2101 BMS:2101" --step --pair{_RESET}')
        return

    prev_idx = _prev_same_index(captures)
    ordinals = _key_ordinals(captures)
    frames, key_a, key_b, n_no_time = _build_pair_frames(captures, tol_s)
    if not frames:
        print("  No timestamped captures to pair.")
        return

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("  (not a TTY — --pair step requires an interactive terminal)")
        return

    console = Console(highlight=False)
    fd = sys.stdin.fileno()

    f = len(frames) - 1  # start at the most recent pair
    status = f"{n_no_time} untimed capture(s) excluded from pairing" if n_no_time else ""

    def redraw() -> None:
        sys.stdout.write("\033[2J\033[H")  # clear + home
        _render_step_pair_frame(
            console,
            captures,
            frames,
            f,
            defs,
            prev_idx,
            ordinals,
            key_a,
            key_b,
            tol_s,
            status=status,
            rulers=rulers,
        )

    with _raw_fullscreen(fd):
        while True:
            redraw()
            status = ""

            key = _read_key(fd)
            if key in ("q", "Q", "\x1b\x1b", "\x1b", "\x03"):  # q / Esc / Ctrl-C
                break
            elif key in ("\x1b[D", "h", "p"):  # left / prev
                if f > 0:
                    f -= 1
                else:
                    status = "At first pair"
            elif key in ("\x1b[C", "l", "n", " "):  # right / next
                if f < len(frames) - 1:
                    f += 1
                else:
                    status = "At last pair"
            elif key in ("\x1b[H", "g"):  # Home / g — first
                f = 0
            elif key in ("\x1b[F", "G"):  # End / G — last
                f = len(frames) - 1
            elif key in ("\x1b[6~", "]"):  # PageDown / ] — forward 100
                f = min(f + 100, len(frames) - 1)
            elif key in ("\x1b[5~", "["):  # PageUp / [ — back 100
                f = max(f - 100, 0)
            elif key in (":", "#"):  # jump to a specific pair number
                buf = ""
                cancelled = False
                while True:
                    redraw_prompt = (
                        f"go to pair # (1-{len(frames)}, Enter=go · Esc=cancel): {buf}\u2588"
                    )
                    sys.stdout.write("\033[2J\033[H")
                    _render_step_pair_frame(
                        console,
                        captures,
                        frames,
                        f,
                        defs,
                        prev_idx,
                        ordinals,
                        key_a,
                        key_b,
                        tol_s,
                        status=redraw_prompt,
                        rulers=rulers,
                    )
                    k = _read_key(fd)
                    if k in ("\r", "\n"):
                        break
                    if k in ("\x1b", "\x03"):
                        cancelled = True
                        break
                    if k in ("\x7f", "\x08"):  # backspace
                        buf = buf[:-1]
                    elif k.isdigit():
                        buf += k
                if cancelled or not buf:
                    status = "Jump cancelled"
                else:
                    n = int(buf)
                    f = max(0, min(n - 1, len(frames) - 1))
                    if not (1 <= n <= len(frames)):
                        status = f"Clamped to {f + 1} (valid: 1-{len(frames)})"
