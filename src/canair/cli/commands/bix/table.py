"""The conversion table and the bare ``canair bix`` overview."""

from __future__ import annotations

from canair.render import ansi
from canair.uds.byteindex import (
    conversion_table,
    wican_to_isotp,
)


def table_role(wican_idx: int, sub_bytes: int) -> str:
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


def pad(text: str, width: int, code: str) -> str:
    """Left-justify ``text`` to ``width`` then color it, so ANSI codes don't
    break the column alignment (padding is applied to the visible text first)."""
    return ansi.c(f"{text:<{width}}", code)


def print_legend(sub_bytes: int, *, show_torque: bool = False, show_obdb: bool = False):
    """Explain the notations and the Role labels in plain language.

    Printed above the compact overview and the full ``--table`` so both are
    self-documenting — a first-time reader never has to guess what ``FF PCI`` or
    ``bix`` mean. The Torque and bix (OBDb) columns are distinct notations, each
    shown only when its flag (``show_torque`` / ``show_obdb``) is set — matching
    the table's default-hidden columns.
    """
    sub_desc = "SID + 2 DID bytes" if sub_bytes == 2 else "SID + 1 PID byte"
    variant = "Torque 2" if sub_bytes == 2 else "Torque 1"
    n_extra = int(show_torque) + int(show_obdb)
    count = {0: "these", 1: "three", 2: "four"}[n_extra]
    print(ansi.c(f"How a UDS response is counted {count} different ways", ansi.BOLD))
    print(
        "  A response rides on CAN frames (8 bytes each) → ISO-TP → UDS. Where you\n"
        "  start counting, and whether you include the transport framing, changes a\n"
        "  byte's index. canair expressions use the WiCAN index.\n"
    )
    print(ansi.c("  Columns", ansi.BOLD))
    print(f"    {pad('WiCAN', 8, ansi.CYAN)}  byte # in the raw CAN frames, framing INCLUDED (Bnn)")
    print("    ISO-TP    byte # in the reassembled payload, framing stripped")
    if show_torque:
        print(f"    Torque    UDS data byte, letter notation ({variant} here: skips {sub_desc})")
        print(
            ansi.c(
                "              ↳ used by the Torque app, Car Scanner & similar OBD apps", ansi.DIM
            )
        )
    if show_obdb:
        print("    bix       OBDb bit index — the UDS data-byte index × 8")
    if show_torque or show_obdb:
        print(
            ansi.c(
                "              ↳ Torque/OBDb count from the first UDS data byte, so the\n"
                "                mapping shifts with the subfunction width: use -1 for\n"
                "                21xx PIDs (Torque 1) and -2 for 22xxxx DIDs (Torque 2).",
                ansi.DIM,
            )
        )
    else:
        print(
            ansi.c(
                "    (add --torque for the Torque letter column, --obdb for the OBDb bix column)",
                ansi.DIM,
            ),
        )
    print()
    print(ansi.c("  Role — what a WiCAN row actually is", ansi.BOLD))
    print(f"    {pad('FF PCI', 8, ansi.DIM)}  First-Frame framing (bytes B00–B01: type + length)")
    print(f"    {pad('CF PCI', 8, ansi.DIM)}  Consecutive-Frame framing (1 byte at B08, B16, B24…)")
    print("    SID       UDS Service ID (the response service byte)")
    if sub_bytes == 2:
        print("    DID       the 2 Data Identifier bytes (UDS subfunction) echoing the request")
    else:
        print("    PID       the Parameter ID byte (UDS subfunction) echoing the request")
    print("    (blank)   real data — the bytes your expression reads")
    print()
    print(
        ansi.c(
            "  PCI = ISO-TP framing bytes. They are NOT data — never index them in an\n"
            "  expression, and never let a multi-byte range straddle one.",
            ansi.DIM,
        )
    )
    print()


def print_table(
    sub_bytes: int,
    max_wican: int = 71,
    *,
    legend: bool = False,
    show_torque: bool = False,
    show_obdb: bool = False,
):
    """Print the conversion table, grouped by CAN frame.

    Each 8-byte CAN frame is separated by a ``── Frame N ──`` divider and a
    ``Role`` column marks the ISO-TP framing (``FF/CF PCI``) and UDS header
    (``SID``/``PID``/``DID``) bytes, so the reader can see where the raw CAN
    frame boundaries fall and which rows are framing vs. real data. With
    ``legend=True`` a plain-language key to the columns and Role labels is
    printed first. The Torque and bix columns are independent opt-ins
    (``show_torque`` / ``show_obdb``); WiCAN/ISO-TP are the primary notations.
    """
    if legend:
        print_legend(sub_bytes, show_torque=show_torque, show_obdb=show_obdb)
    table = conversion_table(max_wican=max_wican, subfunction_bytes=sub_bytes)

    sub_label = f"Torque {sub_bytes}" if sub_bytes in (1, 2) else f"Torque (sub={sub_bytes})"

    # Columns: WiCAN | ISO-TP | [Torque] | [bix] | Role. Torque and bix are
    # separate notations (Torque letter vs OBDb bit-index), each enabled on its
    # own flag; build every row from the enabled columns so header/rule/data
    # widths stay in lockstep (and the frame divider spans the right border).
    def _cells(wican_c, isotp_c, role_c, *, torque_c="", bix_c=""):
        parts = [f"{wican_c:>5}", f"{isotp_c:>6}"]
        if show_torque:
            parts.append(f"{torque_c:>8}")
        if show_obdb:
            parts.append(f"{bix_c:>5}")
        parts.append(f"{role_c:<6}")
        return "| " + " | ".join(parts) + " |"

    rule_parts = ["-" * 7, "-" * 8]
    if show_torque:
        rule_parts.append("-" * 10)
    if show_obdb:
        rule_parts.append("-" * 7)
    rule_parts.append("-" * 8)
    header = _cells("WiCAN", "ISO-TP", "Role", torque_c=sub_label, bix_c="bix")
    rule = "|" + "|".join(rule_parts) + "|"
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
            print(ansi.c(f"|{label}|", ansi.CYAN))
            prev_frame = frame

        role = table_role(w_idx, sub_bytes)
        line = _cells(
            f"B{w_idx:02d}",
            f"0x{row['isotp']:02X}" if row["isotp"] is not None else "",
            role,
            torque_c=row["torque_letter"] or "",
            bix_c=str(row["bix"]) if row["bix"] is not None else "",
        )
        # Dim the ISO-TP framing (PCI) rows so real data stands out.
        print(ansi.c(line, ansi.DIM) if role.endswith("PCI") else line)


def print_overview(sub_bytes: int, *, show_torque: bool = False, show_obdb: bool = False):
    """The friendly bare-``bix`` landing view: legend + a compact 2-frame table.

    Shows just the first two CAN frames (B00–B15) — enough to see the FF/CF PCI
    boundary — and points at the ways to go deeper.
    """
    print_table(sub_bytes, max_wican=15, legend=True, show_torque=show_torque, show_obdb=show_obdb)
    print()
    print(ansi.c("  Go further", ansi.BOLD))
    print("    canair bix --table          full table (all frames, up to --max)")
    print("    canair bix w9               convert one index (WiCAN B09) to every notation")
    print("    canair bix E                convert a Torque letter; also i6, b32, or a number")
    print("    canair bix --annotate HEX   map a real response payload, byte by byte")
    if not show_torque:
        print(
            "    canair bix --torque         add the Torque letter column (Torque app, Car Scanner)"
        )
    if not show_obdb:
        print("    canair bix --obdb           add the OBDb bix (bit-index) column")
    print(
        ansi.c(
            "    canair bix -2 …             same, for 22xxxx DIDs (2-byte subfunction)", ansi.DIM
        )
    )
