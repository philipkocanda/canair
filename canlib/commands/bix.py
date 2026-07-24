"""``canair bix`` — convert byte indices between WiCAN, ISO-TP, Torque, OBDb."""

from __future__ import annotations

import argparse
import re
import sys

from canlib.byteindex import (
    bix_to_wican,
    conversion_table,
    isotp_to_torque,
    isotp_to_wican,
    letter_to_torque_idx,
    payload_to_wican_frame,
    torque_idx_to_letter,
    torque_to_bix,
    torque_to_wican,
    wican_to_bix,
    wican_to_isotp,
    wican_to_torque,
)

NAME = "bix"

# ANSI colors (match the sibling tools: decode, coverage, research).
# Emitted only when stdout is a TTY so piped/redirected output stays plain.
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def _use_color() -> bool:
    return sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    """Wrap ``text`` in an ANSI ``code`` when stdout is a TTY, else return it plain."""
    return f"{code}{text}{_RESET}" if _use_color() else text


def _cerr(text: str, code: str) -> str:
    """Like :func:`_c`, but gated on stderr's TTY-ness (warnings go to stderr)."""
    return f"{code}{text}{_RESET}" if sys.stderr.isatty() else text


_EPILOG = """\
run `canair bix` with no arguments for a guided overview (a legend explaining
each notation + a compact 2-frame table); `--table` prints the full table.

input formats:
  w9, W09     WiCAN byte index (prefix w)
  i6, i0x06   ISO-TP payload index (prefix i)
  b32         Torque bit index / bix (prefix b)
  E, AA       Torque letter notation
  9           Plain number (assumed WiCAN)

subfunction modes:
  -1          1-byte subfunction (21xx PIDs) — default
  -2          2-byte subfunction (22xxxx DIDs)

note: with --annotate/-a, put the mode flag (-1/-2) BEFORE the hex bytes,
      e.g. `bix -2 -a 62 01 A0 ...` — a mode flag after the bytes is
      consumed as another argument."""


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Convert byte indices between WiCAN, ISO-TP, Torque, and OBDb notations",
        description="Convert byte indices between WiCAN, ISO-TP, Torque, and OBDb notations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument("value", nargs="?", help="Index to convert (see formats below)")
    parser.add_argument(
        "-1",
        dest="sub_bytes",
        action="store_const",
        const=1,
        default=1,
        help="1-byte subfunction mode (default)",
    )
    parser.add_argument(
        "-2",
        dest="sub_bytes",
        action="store_const",
        const=2,
        help="2-byte subfunction mode (22xxxx DIDs)",
    )
    parser.add_argument(
        "--table", "-t", action="store_true", help="Print the full conversion table (all frames)"
    )
    parser.add_argument(
        "--annotate",
        "-a",
        metavar="HEX",
        nargs="+",
        help="Annotate a hex payload with all index representations "
        "(e.g. 62B0047402990C0040A000AAAA, or space-separated bytes "
        "62 B0 04 ... quoted or unquoted). Expects the reassembled UDS "
        "response payload (SID-first, ISO-TP PCI stripped) unless --raw.",
    )
    parser.add_argument(
        "--raw",
        "--frame",
        dest="raw_frame",
        action="store_true",
        help="With --annotate: the hex is an ALREADY-FRAMED CAN payload (ISO-TP "
        "PCI bytes present, e.g. straight off the bus) — index it as-is instead "
        "of reconstructing the framing from a PCI-stripped UDS payload.",
    )
    parser.add_argument(
        "--torque",
        "--obdb",
        dest="show_torque",
        action="store_true",
        help="Show the Torque and bix (OBDb) columns too. Hidden by default — "
        "WiCAN and ISO-TP are the notations canair expressions use; Torque/bix "
        "are for cross-referencing third-party Torque/OBDb PID sheets.",
    )
    parser.add_argument(
        "--max", type=int, default=71, help="Max WiCAN index for table (default: 71)"
    )
    parser.add_argument(
        "--ecu",
        help="With --annotate: overlay which defined parameter maps each byte "
        "(and flag unmapped bytes). Requires --pid.",
    )
    parser.add_argument(
        "--pid",
        help="With --annotate --ecu: the PID whose parameters to overlay (e.g. 22BC03).",
    )
    parser.set_defaults(func=run)
    return parser


def _table_role(wican_idx: int, sub_bytes: int) -> str:
    """Framing role of a WiCAN byte in the canonical multi-frame layout.

    Returns ``"FF PCI"``/``"CF PCI"`` for ISO-TP framing bytes, ``"SID"`` /
    ``"PID"`` / ``"DID"`` for the UDS header bytes, or ``""`` for a data byte.
    The table has no concrete payload, so the role is derived purely from the
    fixed multi-frame ISO-TP/UDS layout (matching ``wican_to_isotp``).
    """
    isotp = wican_to_isotp(wican_idx)
    if isotp is None:
        return "FF PCI" if wican_idx < 8 else "CF PCI"
    if isotp == 0:
        return "SID"
    if isotp < 1 + sub_bytes:
        return "DID" if sub_bytes == 2 else "PID"
    return ""


def _parse_input(value: str) -> tuple[str, int]:
    """Parse input value into (notation, index)."""
    v = value.strip()

    m = re.match(r"^([wWiIbB])(\d+|0x[0-9a-fA-F]+)$", v)
    if m:
        prefix = m.group(1).lower()
        idx = int(m.group(2), 0)
        notation = {"w": "wican", "i": "isotp", "b": "bix"}[prefix]
        return notation, idx

    if re.match(r"^[A-Za-z]{1,2}$", v) and not re.match(r"^[wWiIbB]$", v):
        return "torque", letter_to_torque_idx(v)

    try:
        return "wican", int(v, 0)
    except ValueError:
        pass

    print(
        f"Error: cannot parse '{value}'. Use w9, i6, b32, E, AA, or a plain number.",
        file=sys.stderr,
    )
    sys.exit(1)


def _print_result(notation: str, idx: int, sub_bytes: int):
    """Convert from one notation and print all others."""
    if notation == "wican":
        w = idx
    elif notation == "isotp":
        w = isotp_to_wican(idx)
    elif notation == "torque":
        w = torque_to_wican(idx, sub_bytes)
    elif notation == "bix":
        w = bix_to_wican(idx, sub_bytes)
    else:
        raise ValueError(f"Unknown notation: {notation}")

    isotp = wican_to_isotp(w)
    torque = wican_to_torque(w, sub_bytes)
    bix = wican_to_bix(w, sub_bytes)
    letter = torque_idx_to_letter(torque) if torque is not None else None

    sub_label = f"sub={sub_bytes}"
    print(f"  WiCAN:    B{w:02d}  (raw CAN frame index)")
    if isotp is not None:
        print(f"  ISO-TP:   0x{isotp:02X}  (payload index {isotp})")
    else:
        print("  ISO-TP:   —  (PCI byte)")
    if torque is not None:
        print(f"  Torque:   {letter}  (byte {torque}, {sub_label})")
        print(f"  bix:      {bix}  (bit index, {sub_label})")
    else:
        role = "PCI" if isotp is None else "UDS header"
        print(f"  Torque:   —  ({role} byte, {sub_label})")
        print("  bix:      —")

    pci_indices = set(range(0, w + 10, 8))  # PCI at 0, 8, 16, 24, ...
    if isotp is not None:
        if (w + 1) in pci_indices:
            pci = w + 1
            after = w + 2 if (w + 2) not in pci_indices else w + 3
            print(f"\n  ⚠ B{pci:02d} is a PCI byte — [B{w:02d}:B{after:02d}] would include it!")
            print(f"    Use (B{w:02d} << 8) | B{after:02d} instead of [B{w:02d}:B{after:02d}]")
        if (w - 1) in pci_indices and w > 0:
            pci = w - 1
            before = w - 2 if (w - 2) not in pci_indices else w - 3
            if before >= 0:
                print(
                    f"\n  ⚠ B{pci:02d} is a PCI byte — [B{before:02d}:B{w:02d}] would include it!"
                )
                print(
                    f"    Use (B{before:02d} << 8) | B{w:02d} instead of [B{before:02d}:B{w:02d}]"
                )


def _pad(text: str, width: int, code: str) -> str:
    """Left-justify ``text`` to ``width`` then color it, so ANSI codes don't
    break the column alignment (padding is applied to the visible text first)."""
    return _c(f"{text:<{width}}", code)


def _print_legend(sub_bytes: int, *, show_torque: bool = False):
    """Explain the notations and the Role labels in plain language.

    Printed above the compact overview and the full ``--table`` so both are
    self-documenting — a first-time reader never has to guess what ``FF PCI`` or
    ``bix`` mean. The Torque/bix columns (and their explanation) are shown only
    when ``show_torque`` is set, matching the table's default-hidden columns.
    """
    sub_desc = "SID + 2 DID bytes" if sub_bytes == 2 else "SID + 1 PID byte"
    variant = "Torque 2" if sub_bytes == 2 else "Torque 1"
    count = "four" if show_torque else "these"
    print(_c(f"How a UDS response is counted {count} different ways", _BOLD))
    print(
        "  A response rides on CAN frames (8 bytes each) → ISO-TP → UDS. Where you\n"
        "  start counting, and whether you include the transport framing, changes a\n"
        "  byte's index. canair expressions use the WiCAN index.\n"
    )
    print(_c("  Columns", _BOLD))
    print(f"    {_pad('WiCAN', 8, _CYAN)}  byte # in the raw CAN frames, framing INCLUDED (Bnn)")
    print("    ISO-TP    byte # in the reassembled payload, framing stripped")
    if show_torque:
        print(f"    Torque    UDS data byte, letter notation ({variant} here: skips {sub_desc})")
        print("    bix       Torque byte × 8 (bit index; used by Torque / OBDb)")
        print(
            _c(
                "              ↳ Torque/OBDb count from the first UDS data byte, so the\n"
                "                mapping shifts with the subfunction width: use -1 for\n"
                "                21xx PIDs (Torque 1) and -2 for 22xxxx DIDs (Torque 2).",
                _DIM,
            )
        )
    else:
        print(
            _c("    (add --torque for the Torque / bix (OBDb) columns)", _DIM),
        )
    print()
    print(_c("  Role — what a WiCAN row actually is", _BOLD))
    print(f"    {_pad('FF PCI', 8, _DIM)}  First-Frame framing (bytes B00–B01: type + length)")
    print(f"    {_pad('CF PCI', 8, _DIM)}  Consecutive-Frame framing (1 byte at B08, B16, B24…)")
    print("    SID       UDS Service ID (the response service byte)")
    if sub_bytes == 2:
        print("    DID       the 2 Data Identifier bytes (UDS subfunction) echoing the request")
    else:
        print("    PID       the Parameter ID byte (UDS subfunction) echoing the request")
    print("    (blank)   real data — the bytes your expression reads")
    print()
    print(
        _c(
            "  PCI = ISO-TP framing bytes. They are NOT data — never index them in an\n"
            "  expression, and never let a multi-byte range straddle one.",
            _DIM,
        )
    )
    print()


def _print_table(
    sub_bytes: int, max_wican: int = 71, *, legend: bool = False, show_torque: bool = False
):
    """Print the conversion table, grouped by CAN frame.

    Each 8-byte CAN frame is separated by a ``── Frame N ──`` divider and a
    ``Role`` column marks the ISO-TP framing (``FF/CF PCI``) and UDS header
    (``SID``/``PID``/``DID``) bytes, so the reader can see where the raw CAN
    frame boundaries fall and which rows are framing vs. real data. With
    ``legend=True`` a plain-language key to the columns and Role labels is
    printed first. The Torque and bix columns are shown only when ``show_torque``
    is set (WiCAN/ISO-TP are the primary notations).
    """
    if legend:
        _print_legend(sub_bytes, show_torque=show_torque)
    table = conversion_table(max_wican=max_wican, subfunction_bytes=sub_bytes)

    sub_label = f"Torque {sub_bytes}" if sub_bytes in (1, 2) else f"Torque (sub={sub_bytes})"
    if show_torque:
        header = f"| {'WiCAN':>5} | {'ISO-TP':>6} | {sub_label:>8} | {'bix':>5} | {'Role':<6} |"
        rule = f"|{'-' * 7}|{'-' * 8}|{'-' * 10}|{'-' * 7}|{'-' * 8}|"
    else:
        header = f"| {'WiCAN':>5} | {'ISO-TP':>6} | {'Role':<6} |"
        rule = f"|{'-' * 7}|{'-' * 8}|{'-' * 8}|"
    width = len(header)
    print(header)
    print(rule)

    prev_frame = -1
    for row in table:
        w_idx = row["wican"]
        frame = w_idx // 8
        if frame != prev_frame:
            label = f"── Frame {frame} "
            label += "─" * (width - 1 - len(label))  # fill to the table's right border
            print(_c(f"|{label}|", _CYAN))
            prev_frame = frame

        role = _table_role(w_idx, sub_bytes)
        w = f"B{w_idx:02d}"
        isotp = f"0x{row['isotp']:02X}" if row["isotp"] is not None else ""
        if show_torque:
            letter = row["torque_letter"] or ""
            bix = str(row["bix"]) if row["bix"] is not None else ""
            line = f"| {w:>5} | {isotp:>6} | {letter:>8} | {bix:>5} | {role:<6} |"
        else:
            line = f"| {w:>5} | {isotp:>6} | {role:<6} |"
        # Dim the ISO-TP framing (PCI) rows so real data stands out.
        print(_c(line, _DIM) if role.endswith("PCI") else line)


def _parse_hex_payload(raw: str) -> list[int]:
    """Parse a hex string (with or without spaces) into a list of byte values."""
    cleaned = raw.replace(" ", "").strip()
    if len(cleaned) % 2 != 0:
        print(f"Error: odd number of hex characters in '{raw}'.", file=sys.stderr)
        sys.exit(1)
    payload = []
    for i in range(0, len(cleaned), 2):
        token = cleaned[i : i + 2]
        try:
            payload.append(int(token, 16))
        except ValueError:
            print(f"Error: invalid hex byte '{token}' in '{raw}'.", file=sys.stderr)
            sys.exit(1)
    return payload


def _looks_like_pci_first_byte(b: int) -> bool:
    """True if ``b`` looks like an ISO-TP PCI byte (SF/FF/CF/FC frame type).

    The high nibble encodes the frame type (0/1/2/3), so a PCI first byte is
    ``0x00``–``0x3F``. UDS positive-response SIDs are request SID + 0x40, landing
    in ``0x50``–``0x7F`` (0x7F = negative response) — a disjoint range. So a
    first byte < 0x40 is reliably a framing byte, not a UDS response SID.
    """
    return b < 0x40


def _looks_like_uds_sid(b: int) -> bool:
    """True if ``b`` looks like a UDS response SID (positive 0x40–0x7E, or 0x7F).

    Disjoint from :func:`_looks_like_pci_first_byte` — used to warn when a
    PCI-stripped UDS payload is passed to ``--raw`` (which expects framing).
    """
    return 0x40 <= b <= 0x7F


def _emit_warning(headline: str, *detail_lines: str):
    """Print an emphasized, visually separated warning to stderr.

    A blank line above and below sets it apart from the table that follows on
    stdout; a bold-yellow ``⚠ WARNING`` banner and an indented rule draw the eye.
    """
    bar = _cerr("⚠ WARNING", _BOLD + _YELLOW)
    rule = _cerr("  " + "─" * 68, _YELLOW)
    print("", file=sys.stderr)
    print(f"  {bar}  {_cerr(headline, _BOLD)}", file=sys.stderr)
    print(rule, file=sys.stderr)
    for line in detail_lines:
        print(f"  {line}", file=sys.stderr)
    print("", file=sys.stderr)


def _warn_payload_kind_mismatch(first_byte: int, raw_frame: bool):
    """Warn (reliably) when the input's first byte contradicts the chosen mode.

    Ranges are disjoint (see the two predicates), so this never false-positives on
    a well-formed input: a raw frame always starts < 0x40, a UDS response payload
    always starts 0x40–0x7F. It's a warning, not a hard error — an odd fragment
    shouldn't be blocked outright — but it names the likely fix.
    """
    if not raw_frame and _looks_like_pci_first_byte(first_byte):
        _emit_warning(
            f"first byte 0x{first_byte:02X} looks like an ISO-TP PCI byte, not a UDS response SID.",
            "--annotate expects the reassembled UDS payload (SID-first, PCI stripped).",
            f"If this is a raw CAN frame straight off the bus, pass {_cerr('--raw', _BOLD)}.",
        )
    elif raw_frame and _looks_like_uds_sid(first_byte):
        _emit_warning(
            f"first byte 0x{first_byte:02X} looks like a UDS response SID, not an ISO-TP PCI byte.",
            "--raw expects an already-framed CAN payload (PCI present).",
            f"If this is a PCI-stripped UDS payload, drop {_cerr('--raw', _BOLD)}.",
        )


def _annotate_payload(
    payload_hex: str,
    sub_bytes: int,
    params: dict | None = None,
    *,
    raw_frame: bool = False,
    show_torque: bool = False,
) -> int:
    """Annotate each byte of a UDS response payload with WiCAN Bnn indices.

    By default ``payload_hex`` is the **reassembled UDS response payload**
    (SID-first, ISO-TP PCI bytes stripped — what the transport/captures hand
    back); the framing is reconstructed to show the WiCAN indices. With
    ``raw_frame=True`` the hex is an **already-framed CAN payload** (PCI present,
    e.g. copied straight off the bus) and is indexed as-is.

    The Torque and bix (OBDb) columns are shown only when ``show_torque`` is set —
    WiCAN and ISO-TP are the notations canair expressions use.

    When ``params`` (a PID's ``parameters`` dict) is given, add a ``Param`` column
    showing which defined parameter maps each byte (``[NAME]`` verified,
    ``[NAME?]`` unverified, ``[NAME:k]`` a specific bit), and mark data bytes no
    param reads as ``unmapped`` — the overlay that makes a wrong byte offset
    obvious at a glance.
    """
    from canlib.byteindex import NotAFrameError, framed_to_wican_frame, mapped_bits, mapped_offsets

    payload_bytes = _parse_hex_payload(payload_hex)
    if payload_bytes:
        _warn_payload_kind_mismatch(payload_bytes[0], raw_frame)
    if raw_frame:
        try:
            frame = framed_to_wican_frame(payload_bytes)
        except NotAFrameError as e:
            print(f"Error: {e}.", file=sys.stderr)
            return 1
    else:
        frame = payload_to_wican_frame(payload_bytes)

    header_size = 1 + sub_bytes
    overlay = params is not None
    mapped = mapped_offsets(params) if overlay else {}
    mbits = mapped_bits(params) if overlay else {}

    if show_torque:
        # Name the active Torque variant so it's clear the Torque/bix mapping is
        # not fixed — it depends on the subfunction width (Torque 1 for 21xx,
        # Torque 2 for 22xxxx). Torque/OBDb count from the first UDS *data* byte.
        variant = "Torque 2" if sub_bytes == 2 else "Torque 1"
        skipped = "SID + 2 DID bytes" if sub_bytes == 2 else "SID + 1 PID byte"
        print(
            _c(
                f"  Torque column = {variant} (skips {skipped}); "
                f"pass -{3 - sub_bytes} for the other variant.",
                _DIM,
            )
        )

    # Column widths, shared by header / separator / data rows so everything lines
    # up (the Role cell must be padded to width, else the trailing Param divider
    # floats). Torque is a letter (A, AB…), bix up to 3 digits, Role up to 6 (FF
    # PCI / CF PCI). Torque + bix are omitted entirely unless show_torque.
    W_WICAN, W_HEX, W_ISOTP, W_TORQUE, W_BIX, W_ROLE = 5, 4, 6, 6, 5, 6

    def _row(wican, hex_, isotp, role, param=None, *, torque=None, bix=None):
        line = f"  {wican:>{W_WICAN}} | {hex_:>{W_HEX}} | {isotp:>{W_ISOTP}} | "
        if show_torque:
            line += f"{torque:>{W_TORQUE}} | {bix:>{W_BIX}} | "
        line += f"{role:<{W_ROLE}}"
        if param is not None:
            line += f" | {param}"
        return line.rstrip()

    def _seg():
        parts = [f"{'─' * W_WICAN}─", f"─{'─' * W_HEX}─", f"─{'─' * W_ISOTP}─"]
        if show_torque:
            parts += [f"─{'─' * W_TORQUE}─", f"─{'─' * W_BIX}─"]
        parts.append(f"─{'─' * W_ROLE}")
        return "  " + "┼".join(parts)

    hdr = _row(
        "WiCAN",
        "Hex",
        "ISO-TP",
        "Role",
        "Param" if overlay else None,
        torque="Torque",
        bix="bix",
    )
    seg = _seg()
    divider_width = len(seg)  # span the fixed columns (up to Role), before Param
    if overlay:
        seg += "─┼─" + "─" * 12
    print(hdr)
    print(seg)

    prev_frame = -1
    for w, (byte_val, pi) in enumerate(frame):
        cur_frame = w // 8
        if cur_frame != prev_frame and len(frame) > 8:
            # Only mark boundaries for genuinely multi-frame responses.
            label = f"  ── Frame {cur_frame} "
            label += "─" * max(0, divider_width - len(label))
            print(_c(label, _CYAN))
            prev_frame = cur_frame

        # Derive every notation from the byte's ACTUAL ISO-TP index (pi) in the
        # reconstructed frame, so single-frame (1 PCI byte) and multi-frame
        # (2 FF + 1/CF PCI bytes) payloads are both correct. The length-agnostic
        # wican_to_isotp() assumes the multi-frame layout and is off-by-one for
        # single-frame responses — using pi keeps this column in step with Role.
        isotp = pi
        torque = isotp_to_torque(pi, sub_bytes) if pi is not None else None
        bix = torque_to_bix(torque) if torque is not None else None
        letter = torque_idx_to_letter(torque) if torque is not None else None

        role = ""
        if pi is None:
            role = "PCI"
        elif pi == 0:
            role = "SID"
        elif pi < header_size:
            role = "DID" if sub_bytes == 2 else "PID"

        param = _param_cell(w, byte_val, role, mapped, mbits) if overlay else None
        line = _row(
            f"B{w:02d}",
            f"0x{byte_val:02X}",
            f"0x{isotp:02X}" if isotp is not None else "—",
            role,
            param,
            torque=letter if letter else "—",
            bix=str(bix) if bix is not None else "—",
        )
        print(_c(line, _DIM) if role == "PCI" else line)
    return 0


def _param_cell(offset: int, byte_val: int, role: str, mapped: dict, mbits: dict) -> str:
    """The Param-overlay cell for one byte: covering param(s), or 'unmapped'."""
    if role in ("PCI", "SID", "DID", "PID"):
        return ""  # framing byte — never a parameter
    bit_hits = sorted((k, mbits[(offset, k)]) for k in range(8) if (offset, k) in mbits)
    parts = []
    byte_map = mapped.get(offset)
    if byte_map and not bit_hits:
        parts.append(f"[{byte_map[0]}]" if byte_map[1] else f"[{byte_map[0]}?]")
    for k, (name, verified) in bit_hits:
        parts.append(f"[{name}:{k}]" if verified else f"[{name}?:{k}]")
    if not parts:
        return "unmapped"
    return " ".join(parts)


def _print_overview(sub_bytes: int, *, show_torque: bool = False):
    """The friendly bare-``bix`` landing view: legend + a compact 2-frame table.

    Shows just the first two CAN frames (B00–B15) — enough to see the FF/CF PCI
    boundary — and points at the ways to go deeper.
    """
    _print_table(sub_bytes, max_wican=15, legend=True, show_torque=show_torque)
    print()
    print(_c("  Go further", _BOLD))
    print("    canair bix --table          full table (all frames, up to --max)")
    print("    canair bix w9               convert one index (WiCAN B09) to every notation")
    print("    canair bix E                convert a Torque letter; also i6, b32, or a number")
    print("    canair bix --annotate HEX   map a real response payload, byte by byte")
    if not show_torque:
        print("    canair bix --torque         add the Torque / bix (OBDb) columns")
    print(_c("    canair bix -2 …             same, for 22xxxx DIDs (2-byte subfunction)", _DIM))


def run(args) -> int:
    if args.table:
        _print_table(args.sub_bytes, args.max, legend=True, show_torque=args.show_torque)
        return 0

    if args.annotate:
        params = None
        if args.ecu:
            if not args.pid:
                print("Error: --ecu requires --pid.", file=sys.stderr)
                return 1
            from canlib.ecus import canonical_ecu_name_safe
            from canlib.pids import build_ecu_index, load_pids

            ecu = canonical_ecu_name_safe(args.ecu).upper()
            pid = args.pid.upper()
            idx = build_ecu_index(load_pids())
            params = idx.get(ecu, {}).get("pids", {}).get(pid, {}).get("parameters", {})
            if not params:
                print(
                    f"Note: no defined parameters for {ecu} {pid} — showing unmapped overlay.",
                    file=sys.stderr,
                )
                params = {}
        elif args.pid:
            print("Error: --pid requires --ecu.", file=sys.stderr)
            return 1
        return _annotate_payload(
            " ".join(args.annotate),
            args.sub_bytes,
            params,
            raw_frame=args.raw_frame,
            show_torque=args.show_torque,
        )

    if args.raw_frame:
        print("Error: --raw only applies to --annotate.", file=sys.stderr)
        return 1

    if not args.value:
        _print_overview(args.sub_bytes, show_torque=args.show_torque)
        return 0

    notation, idx = _parse_input(args.value)
    _print_result(notation, idx, args.sub_bytes)
    return 0
