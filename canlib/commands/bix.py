"""``canair bix`` — convert byte indices between WiCAN, ISO-TP, Torque, OBDb."""

from __future__ import annotations

import argparse
import re
import sys

from canlib.byteindex import (
    bix_to_wican,
    conversion_table,
    isotp_to_wican,
    letter_to_torque_idx,
    payload_to_wican_frame,
    torque_idx_to_letter,
    torque_to_wican,
    wican_to_bix,
    wican_to_isotp,
    wican_to_torque,
)

NAME = "bix"

_EPILOG = """\
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
    parser.add_argument("--table", "-t", action="store_true", help="Print full conversion table")
    parser.add_argument(
        "--annotate",
        "-a",
        metavar="HEX",
        nargs="+",
        help="Annotate a hex payload with all index representations "
        "(e.g. 62B0047402990C0040A000AAAA, or space-separated bytes "
        "62 B0 04 ... quoted or unquoted)",
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


def _print_table(sub_bytes: int, max_wican: int = 71):
    """Print the full conversion table."""
    table = conversion_table(max_wican=max_wican, subfunction_bytes=sub_bytes)

    sub_label = f"Torque {sub_bytes}" if sub_bytes in (1, 2) else f"Torque (sub={sub_bytes})"
    print(f"| {'WiCAN':>5} | {'ISO-TP':>6} | {sub_label:>8} | {'bix':>5} |")
    print(f"|{'-' * 7}|{'-' * 8}|{'-' * 10}|{'-' * 7}|")

    for row in table:
        w = f"B{row['wican']:02d}"
        isotp = f"0x{row['isotp']:02X}" if row["isotp"] is not None else ""
        letter = row["torque_letter"] or ""
        bix = str(row["bix"]) if row["bix"] is not None else ""
        print(f"| {w:>5} | {isotp:>6} | {letter:>8} | {bix:>5} |")


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


def _annotate_payload(payload_hex: str, sub_bytes: int, params: dict | None = None):
    """Annotate each byte of a UDS response payload with WiCAN Bnn indices.

    When ``params`` (a PID's ``parameters`` dict) is given, add a ``Param`` column
    showing which defined parameter maps each byte (``[NAME]`` verified,
    ``[NAME?]`` unverified, ``[NAME:k]`` a specific bit), and mark data bytes no
    param reads as ``unmapped`` — the overlay that makes a wrong byte offset
    obvious at a glance.
    """
    from canlib.byteindex import mapped_bits, mapped_offsets

    payload_bytes = _parse_hex_payload(payload_hex)
    frame = payload_to_wican_frame(payload_bytes)

    header_size = 1 + sub_bytes
    overlay = params is not None
    mapped = mapped_offsets(params) if overlay else {}
    mbits = mapped_bits(params) if overlay else {}

    hdr = f"  {'WiCAN':>5} | {'Hex':>4} | {'ISO-TP':>6} | {'Torque':>6} | {'bix':>5} | Role"
    sep = f"  {'─' * 5}─┼─{'─' * 4}─┼─{'─' * 6}─┼─{'─' * 6}─┼─{'─' * 5}─┼─{'─' * 10}"
    if overlay:
        hdr += " | Param"
        sep += "─┼─" + "─" * 12
    print(hdr)
    print(sep)

    for w, (byte_val, pi) in enumerate(frame):
        isotp = wican_to_isotp(w)
        torque = wican_to_torque(w, sub_bytes)
        bix = wican_to_bix(w, sub_bytes)
        letter = torque_idx_to_letter(torque) if torque is not None else None

        role = ""
        if pi is None:
            role = "PCI"
        elif pi == 0:
            role = "SID"
        elif pi < header_size:
            role = "DID" if sub_bytes == 2 else "PID"

        w_str = f"B{w:02d}"
        iso_str = f"0x{isotp:02X}" if isotp is not None else "—"
        t_str = letter if letter else "—"
        b_str = str(bix) if bix is not None else "—"
        line = (
            f"  {w_str:>5} | 0x{byte_val:02X} |  {iso_str:>5} |  {t_str:>5} | {b_str:>5} | {role}"
        )
        if overlay:
            line += f" | {_param_cell(w, byte_val, role, mapped, mbits)}"
        print(line)


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


def run(args) -> int:
    if args.table:
        _print_table(args.sub_bytes, args.max)
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
        _annotate_payload(" ".join(args.annotate), args.sub_bytes, params)
        return 0

    if not args.value:
        print("Error: provide an index to convert, --table, or --annotate.", file=sys.stderr)
        return 1

    notation, idx = _parse_input(args.value)
    _print_result(notation, idx, args.sub_bytes)
    return 0
