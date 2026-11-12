"""Output formatting helpers."""

import json
from collections.abc import Collection
from typing import Literal

from rich.text import Text

from canair.expression.decoding import ParamRow
from canair.expression.notation import ByteDisplay
from canair.privacy.sensitive import MASK, MASKED_BYTE
from canair.uds.byteindex import extract_byte_indices, isotp_to_wican, wican_to_elm_idx
from canair.uds.response import UdsResponse

# Which byte space a raw buffer is in. Both are plain ``bytes`` and they differ by
# the ISO-TP PCI framing bytes, so the distinction has to be carried explicitly —
# mixing them silently shifts every WiCAN ``Bnn`` label (see print_hexdump).
ByteLayout = Literal["isotp", "wican"]

# Map a base byte colour → its highlighted (changed-byte) variant.
_HIGHLIGHT_STYLE = {
    "green": "bold white on dark_green",
    "yellow": "bold white on dark_goldenrod",
    "bright_black": "bold white on grey37",
}

# A declared support map's bytes (``support_map:`` on the PID; canair.uds.support_map): muted
# and grey-blue, so they read as layout metadata rather than as uncovered data. A map byte that
# changes is worth noticing (another car or software), so it still flashes.
MAP_BYTE_STYLE = "light_slate_grey"
_HIGHLIGHT_STYLE[MAP_BYTE_STYLE] = "bold white on medium_purple4"

# What each byte colour means, for a TUI's help legend: (sample, style, meaning). One list
# beside the styles it describes, so the legend cannot drift from the colours.
BYTE_COLOUR_LEGEND: tuple[tuple[str, str, str], ...] = (
    ("3C", "green", "byte read by a verified signal"),
    ("3C", "yellow", "byte read by an unverified signal"),
    ("3C", "bright_black", "byte no signal reads"),
    ("3C", MAP_BYTE_STYLE, "support map byte: marks which data are signals"),
    ("3C", _HIGHLIGHT_STYLE["green"], "byte changed since the previous payload"),
)


def _bytes_to_ascii(raw_hex: str) -> str:
    """Convert hex string to ASCII representation (printable chars or '.'); ``**`` shows ``*``."""
    data = bytes.fromhex(raw_hex.replace(MASKED_BYTE, b"*".hex()))
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _build_byte_colors(
    params: list[ParamRow], n_bytes: int, map_bytes: Collection[int] = ()
) -> list[str]:
    """Return a per-ELM-byte colour list based on parameter coverage and verification.

    Priority: green (covered by verified param) > yellow (covered by unverified) >
              :data:`MAP_BYTE_STYLE` (a declared support-map byte) >
              bright_black (not covered by any expression).

    Args:
        params:    list of (name, value, unit, expression, error, verified[, display])
        n_bytes:   number of ELM payload bytes
        map_bytes: payload indices of the PID's declared support-map bytes
    Returns:
        list of Rich colour strings, one per byte index.
    """
    # 0 = uncovered, 1 = unverified, 2 = verified
    rank = [0] * n_bytes
    for row in params:
        expression, perr, verified = row[3], row[4], row[5]
        if perr or not expression:
            continue
        level = 2 if verified else 1
        for wi in extract_byte_indices(expression):
            ei = wican_to_elm_idx(wi, n_bytes)
            if ei is not None and 0 <= ei < n_bytes:
                if level > rank[ei]:
                    rank[ei] = level
    color_map = {0: "bright_black", 1: "yellow", 2: "green"}
    return [
        MAP_BYTE_STYLE if r == 0 and i in map_bytes else color_map[r] for i, r in enumerate(rank)
    ]


def _changed_byte_offsets(raw_hex: str, prev_raw: str) -> set[int]:
    """Byte positions whose value differs from ``prev_raw`` (empty if no prior)."""
    if not prev_raw:
        return set()
    cur = [raw_hex[i : i + 2] for i in range(0, len(raw_hex), 2)]
    prev = [prev_raw[i : i + 2] for i in range(0, len(prev_raw), 2)]
    return {i for i, hb in enumerate(cur) if i < len(prev) and prev[i] != hb}


def changed_param_highlights(
    params: list[ParamRow],
    raw_hex: str,
    prev_raw: str,
    prev_values: dict[str, object] | None = None,
) -> dict[str, str]:
    """Map each parameter whose *decoded value* changed to its highlight style.

    A parameter is highlighted only when its decoded value differs from the
    previous cycle's — not merely when a raw byte it reads changed. This avoids
    flagging a param when a byte flips in a bit the param doesn't map, or when a
    sub-resolution raw change rounds to the same displayed value. When
    ``prev_values`` is ``None`` (no prior decoded snapshot, e.g. the first cycle),
    it falls back to the raw-byte-change heuristic so the very first change still
    highlights.

    The style is the parameter's own coverage-background variant
    (verified→dark_green, unverified→dark_goldenrod), matching the background the
    changed byte itself gets in the hex line so the visual link is exact. Returns
    ``{name: style}`` (empty when nothing changed / no prior payload).
    """
    changed = _changed_byte_offsets(raw_hex, prev_raw)
    if not changed:
        return {}
    n_bytes = len(raw_hex) // 2
    out: dict[str, str] = {}
    for row in params:
        name, value, expression, perr, verified = row[0], row[1], row[3], row[4], row[5]
        if perr or not expression:
            continue
        offsets = param_byte_indices(expression, n_bytes)
        if not changed.intersection(offsets):
            continue
        # A byte the param reads changed; only highlight if the *decoded* value
        # actually moved. With no prior decoded snapshot, fall back to the
        # raw-change signal (first-change highlight).
        if prev_values is not None and name in prev_values and prev_values[name] == value:
            continue
        base = "green" if verified else "yellow"
        out[name] = _HIGHLIGHT_STYLE.get(base, base)
    return out


def param_byte_indices(expression: str, n_bytes: int) -> list[int]:
    """Return the ELM payload byte positions a WiCAN expression reads.

    Maps each WiCAN byte index in ``expression`` to its position in the raw
    payload hex (ISO-TP/ELM index), so the result lines up with the byte columns
    shown in the hex view. Out-of-range indices are dropped. Sorted ascending.
    """
    elm: set[int] = set()
    for wi in extract_byte_indices(expression):
        ei = wican_to_elm_idx(wi, n_bytes)
        if ei is not None and 0 <= ei < n_bytes:
            elm.add(ei)
    return sorted(elm)


def param_byte_index_str(expression: str, display: ByteDisplay) -> str:
    """Human-readable byte reference for a signal's expression.

    Valid payload positions are rendered as compact ranges in ``display``'s
    notation (e.g. ``B18-B19``), so the column names bytes in the *same* space as
    the ruler drawn above the hex. WiCAN indices that don't map to a payload byte
    — PCI/ISO-TP framing bytes, or indices beyond the payload — are flagged as
    ``⚠B<idx>`` (a common definition bug: reading a framing byte as data).
    Returns ``""`` if nothing is referenced.
    """
    valid: list[int] = []
    bad_wican: list[int] = []
    for wi in sorted(extract_byte_indices(expression)):
        ei = wican_to_elm_idx(wi, display.payload_len)
        if ei is not None and 0 <= ei < display.payload_len:
            valid.append(ei)
        else:
            bad_wican.append(wi)
    parts: list[str] = []
    if valid:
        parts.append(
            ",".join(
                display.ref(start, end if end != start else None)
                for start, end in _rendered_runs(sorted(set(valid)), display)
            )
        )
    if bad_wican:
        parts.append("⚠B" + ",".join(str(w) for w in bad_wican))
    return " ".join(parts)


def _rendered_runs(indices: list[int], display: ByteDisplay) -> list[tuple[int, int]]:
    """Split payload offsets into runs that are *adjacent as rendered*.

    Contiguity is judged in the display notation's own sequence, not the payload's:
    WiCAN interleaves PCI framing bytes, so payload bytes 12..15 must render as
    ``B15,B17-B19`` rather than a ``B15-B19`` that would claim the PCI byte.
    """
    runs: list[tuple[int, int]] = []
    if not indices:
        return runs
    start = prev = indices[0]
    for i in indices[1:]:
        if i == prev + 1 and display.ordinal(i) == display.ordinal(prev) + 1:
            prev = i
            continue
        runs.append((start, prev))
        start = prev = i
    runs.append((start, prev))
    return runs


def format_value(value: float | str | None, unit: str, display: str = "") -> str:
    """Format a decoded value with unit and optional display expression.

    If display is set, evaluates it as an f-string with v=value and appends
    the formatted result in parentheses: "480 min (08:00)".

    ``value`` is a ``ParamRow`` value, so it may be ``None`` (a decode that
    produced no numeric result); that (and any stray non-numeric) is rendered
    as-is rather than crashing the render.
    """
    if not isinstance(value, (int, float)):
        # None (a decode with no numeric result) or a non-numeric typed/enum
        # label / stray string — render as-is rather than crashing compose().
        return f"{value} {unit}".strip()
    try:
        is_int = value == int(value)
    except (ValueError, OverflowError):
        # NaN / inf aren't representable as int — render the float form as-is.
        return f"{value} {unit}".strip()
    if is_int:
        base = f"{int(value)} {unit}".strip()
    else:
        base = f"{value:.2f} {unit}".strip()
    if display:
        try:
            v = value  # noqa: F841 — used in eval
            formatted = eval(display)
            base = f"{base} ({formatted})"
        except Exception:
            pass  # silently skip broken display expressions
    return base


def render_hex_line(
    raw_hex: str,
    params: list[ParamRow],
    unmapped: bool,
    *,
    prev_raw: str = "",
    prefix: str = "      ",
    prefix_style: str = "",
    suffix: str = "",
    map_bytes: Collection[int] = (),
) -> Text:
    """Render a payload hex line as Rich Text with per-byte change highlighting.

    Bytes get a base colour from parameter coverage (green=verified, yellow=
    unverified, grey=uncovered); bytes differing from ``prev_raw`` get a
    highlighted background variant. Unmapped/paramless payloads render grey with
    a trailing ASCII column. ``map_bytes`` (payload indices of the PID's declared
    support map) render in :data:`MAP_BYTE_STYLE` unless a signal reads them.
    ``prefix`` is prepended before the hex bytes and ``suffix`` (dimmed) appended
    after them.
    """
    elm_bytes = [raw_hex[i : i + 2] for i in range(0, len(raw_hex), 2)]
    prev_bytes = [prev_raw[i : i + 2] for i in range(0, len(prev_raw), 2)] if prev_raw else []
    n_bytes = len(elm_bytes)
    t = Text()
    t.append(prefix, style=prefix_style)

    if raw_hex == MASK:
        t.append(f"{MASK}  (sensitive — read --reveal to show)", style="bright_black")
    elif unmapped or not params:
        for i, hb in enumerate(elm_bytes):
            if i > 0:
                t.append(" ")
            changed = i < len(prev_bytes) and prev_bytes[i] != hb
            base = MAP_BYTE_STYLE if i in map_bytes else "bright_black"
            style = _HIGHLIGHT_STYLE[base] if changed else base
            t.append(hb, style=style)
        ascii_repr = _bytes_to_ascii(raw_hex)
        t.append(f"  {ascii_repr}  ({n_bytes} B)", style="bright_black")
    else:
        byte_color = _build_byte_colors(params, n_bytes, map_bytes)
        for i, hb in enumerate(elm_bytes):
            if i > 0:
                t.append(" ")
            base = "bright_black" if hb == MASKED_BYTE else byte_color[i]
            changed = i < len(prev_bytes) and prev_bytes[i] != hb
            style = _HIGHLIGHT_STYLE.get(base, base) if changed else base
            t.append(hb, style=style)
        t.append(f"  ({n_bytes} B)", style="bright_black")

    if suffix:
        t.append(suffix, style="dim")
    t.append("\n")
    return t


def render_byte_rulers(
    display: ByteDisplay,
    params: list[ParamRow],
    *,
    prefix_width: int = 8,
    map_bytes: Collection[int] = (),
) -> Text:
    """Return a byte-index ruler row for a payload, as Rich Text.

    **One** row is drawn, in ``display``'s notation (the user's
    ``display.byte_notation``, WiCAN by default) and labelled with it — showing
    two rows in two different spaces made the columns ambiguous rather than
    clearer, and the signal byte-reference column beside each value is rendered
    from the same :class:`~canair.expression.notation.ByteDisplay`, so the two always agree.

    Numbers are coverage-coloured (green=verified, yellow=unverified,
    grey=uncovered, :data:`MAP_BYTE_STYLE` for ``map_bytes``) to match the hex
    view, on a dark background bar, and aligned under each hex byte column.

    ``prefix_width`` must equal the width of the prefix used on the hex lines
    rendered below (e.g. timestamp column) so the ruler columns line up. The row
    is newline-terminated.
    """
    n_bytes = display.payload_len
    bg = "on grey23"
    # Lift uncovered grey to a readable tone against the dark background.
    fg_map = {"green": "green", "yellow": "yellow", "bright_black": "grey58"}
    byte_colors = _build_byte_colors(params, n_bytes, map_bytes) if params or map_bytes else None

    out = Text()
    # Label sits in the leftmost 6 chars, padded out to the full prefix width.
    out.append(f"{display.row_label:>6}".ljust(prefix_width), style=f"bold white {bg}")
    for i in range(n_bytes):
        if i > 0:
            out.append(" ", style=bg)
        base = byte_colors[i] if byte_colors else "bright_black"
        out.append(display.cell(i), style=f"{fg_map.get(base, base)} {bg}")
    out.append("\n")
    return out


def print_hexdump(data: bytes, prefix: str = "  ", *, layout: ByteLayout = "isotp"):
    """Print a hex dump of raw bytes with WiCAN byte indices.

    ``layout`` names the space ``data`` is in, because the two differ by the PCI
    framing bytes and both are plain ``bytes``:

    - ``"isotp"`` (default) — a reassembled UDS payload, PCI already stripped
      (what every transport returns). Labels are derived with
      :func:`~canair.uds.byteindex.isotp_to_wican`, which re-inserts the framing
      offsets: PCI occupies WiCAN 0-1 (first frame) and 8, 16, 24, … (each
      consecutive frame).
    - ``"wican"`` — a buffer already in the WiCAN AutoPID layout (PCI present,
      e.g. from :func:`~canair.uds.wican_layout.uds_hex_to_wican_bytes`). Its index
      *is* the WiCAN index, so labels are the identity.

    Passing a WiCAN-layout buffer without ``layout="wican"`` shifts every label
    by the framing offset — silently, since both are ``bytes``.
    """

    def label(j: int) -> int:
        return j if layout == "wican" else isotp_to_wican(j)

    for row_start in range(0, len(data), 16):
        row_end = min(row_start + 16, len(data))
        hex_part = " ".join(f"{data[j]:02X}" for j in range(row_start, row_end))
        bnn_part = " ".join(f"B{label(j):02d}" for j in range(row_start, row_end))
        print(f"{prefix}Bnn:  {bnn_part}")
        print(f"{prefix}Hex:   {hex_part}")
        print()


def format_raw_with_bnn(data: bytes) -> str:
    """Format ISO-TP payload bytes with WiCAN Bnn index labels.

    Returns a string like: B02=62 B03=BC B04=03 B05=FD ...
    Helps avoid byte offset confusion when reading raw output.
    """

    parts = []
    for i, b in enumerate(data):
        bnn = isotp_to_wican(i)
        parts.append(f"B{bnn:02d}={b:02X}")
    return " ".join(parts)


def print_json_result(result: UdsResponse):
    """Print result as JSON for machine consumption."""
    out = {}
    for k, v in result.items():
        if isinstance(v, bytes):
            out[k] = v.hex().upper()
        else:
            out[k] = v
    print(json.dumps(out, indent=2))
