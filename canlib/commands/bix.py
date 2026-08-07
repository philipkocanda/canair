"""``canair bix`` — convert byte indices between WiCAN, ISO-TP, Torque, OBDb."""

from __future__ import annotations

import argparse
import re
import sys

from canlib import ansi
from canlib.byteindex import (
    bix_to_wican,
    conversion_table,
    isotp_to_torque,
    isotp_to_wican,
    letter_to_torque_idx,
    payload_to_wican_frame,
    torque_label,
    torque_to_bix,
    torque_to_wican,
    wican_to_bix,
    wican_to_isotp,
    wican_to_torque,
)
from canlib.notation import subfunction_bytes_for_pid
from canlib.uds_layout import (
    ROLE_DID,
    ROLE_PCI,
    ROLE_PID,
    ROLE_SID,
    ResponseLayout,
    nrc_name,
    response_layout,
    role_definitions,
)
from canlib.uds_services import service_response_name

NAME = "bix"


# ANSI colors (match the sibling tools: decode, coverage, research).
# Emitted only when stdout is a TTY so piped/redirected output stays plain.
def _use_color() -> bool:
    return sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    """Wrap ``text`` in an ANSI ``code`` when stdout is a TTY, else return it plain."""
    return f"{code}{text}{ansi.RESET}" if _use_color() else text


def _cerr(text: str, code: str) -> str:
    """Like :func:`_c`, but gated on stderr's TTY-ness (warnings go to stderr)."""
    return f"{code}{text}{ansi.RESET}" if sys.stderr.isatty() else text


_EPILOG = """\
run `canair bix` with no arguments for a guided overview (a legend explaining
each notation + a compact 2-frame table); `--table` prints the full table.

input formats:
  w9, W09     WiCAN byte index (prefix w)
  B09         WiCAN byte index (uppercase B — the Bnn convention)
  i6, i0x06   ISO-TP payload index (prefix i)
  b32         OBDb bix / bit index (lowercase b)
  E, AA       Torque letter notation (Torque app, Car Scanner & similar apps)
  9           Plain number (assumed WiCAN)

subfunction modes:
  -1          1-byte subfunction (21xx PIDs) — default
  -2          2-byte subfunction (22xxxx DIDs)

  with a `--pid` that names its service (`22xxxx` / `21xx`) the width is DERIVED
  from it, so `bix -a HEX --ecu IGPM --pid 22BC03` needs no -2; an explicit -1/-2
  still wins (and is flagged when it contradicts the PID). A short-form DID
  (`B004`) doesn't state its service — pass -2 yourself.

optional columns (--table / --annotate; both hidden by default):
  --torque    add the Torque letter column (Torque app, Car Scanner & similar)
  --obdb      add the OBDb bix (bit-index) column — a distinct notation

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
    # default=None (not 1) so run() can tell an EXPLICIT -1 from an unset flag and
    # fall back to deriving the width from --pid; _resolve_sub_bytes applies the 1.
    parser.add_argument(
        "-1",
        dest="sub_bytes",
        action="store_const",
        const=1,
        default=None,
        help="1-byte subfunction mode (default, unless derived from --pid)",
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
        dest="show_torque",
        action="store_true",
        help="Show the Torque byte-letter column (--table/--annotate). Hidden by "
        "default. Torque notation (A, B, C… from the first UDS data byte) is what "
        "the Torque app, Car Scanner, and similar OBD apps use — handy for porting "
        "their PID sheets.",
    )
    parser.add_argument(
        "--obdb",
        dest="show_obdb",
        action="store_true",
        help="Show the OBDb bix (bit-index) column (--table/--annotate). Hidden by "
        "default. bix is a distinct notation from Torque (data-byte index × 8) — "
        "request it independently of --torque.",
    )
    parser.add_argument(
        "--no-legend",
        dest="legend",
        action="store_false",
        help="With --annotate: omit the trailing definition list of the Role terms "
        "(PCI/SID/DID/LID/RID/NRC/…) that appear in the payload.",
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
        help="With --annotate --ecu: the PID whose parameters to overlay (e.g. 22BC03). "
        "Also sets the subfunction width when it names its service (22xxxx DID → 2 "
        "bytes, 21xx → 1) unless -1/-2 is passed explicitly.",
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
        prefix = m.group(1)
        num = m.group(2)
        idx = int(num, 16) if num.lower().startswith("0x") else int(num, 10)
        # Case-sensitive B/b: uppercase `B09` is WiCAN (the Bnn convention used
        # throughout canair), lowercase `b32` is the OBDb bix index. w/W and i/I
        # are case-insensitive.
        notation = {
            "w": "wican",
            "W": "wican",
            "i": "isotp",
            "I": "isotp",
            "B": "wican",
            "b": "bix",
        }[prefix]
        return notation, idx

    if re.match(r"^[A-Za-z]{1,2}$", v) and not re.match(r"^[wWiIbB]$", v):
        return "torque", letter_to_torque_idx(v)

    try:
        return "wican", int(v, 16) if v.lower().startswith("0x") else int(v, 10)
    except ValueError:
        pass

    print(
        f"Error: cannot parse '{value}'. Use w9, B9, i6, b32, E, AA, or a plain number.",
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
    letter = torque_label(torque)

    frame = w // 8
    lo, hi = frame * 8, frame * 8 + 7
    sub_label = f"sub={sub_bytes}"
    print(f"  {'WiCAN:':<11}B{w:02d}  (WiCAN AutoPID frame index: ISO-TP + PCI)")
    print(
        f"  {'CAN frame:':<11}{frame}   "
        f"(B{w:02d} is in CAN frame {frame}: B{lo:02d}–B{hi:02d}, 8 bytes per frame)"
    )
    if isotp is not None:
        print(f"  {'ISO-TP:':<11}0x{isotp:02X}  (payload index {isotp})")
    else:
        print(f"  {'ISO-TP:':<11}—  (PCI framing byte)")
    if torque is not None:
        print(
            f"  {'Torque:':<11}{letter}  (data byte {torque}, {sub_label}; Torque app / Car Scanner)"
        )
        print(f"  {'bix:':<11}{bix}  (OBDb bit index, {sub_label})")
    else:
        role = "PCI" if isotp is None else "UDS header"
        print(f"  {'Torque:':<11}—  ({role} byte, {sub_label})")
        print(f"  {'bix:':<11}—")

    # A neighbour being a PCI (ISO-TP framing) byte means a naive [Bx:By] range
    # would fold it in. Detect PCI with the canonical wican_to_isotp (shared with
    # the table/annotate/analysis paths) rather than a hand-rolled `% 8` heuristic,
    # so every PCI position — including the first frame's second byte at B01 — is
    # covered consistently.
    def _is_pci(idx: int) -> bool:
        return idx >= 0 and wican_to_isotp(idx) is None

    if isotp is not None:
        if _is_pci(w + 1):
            pci = w + 1
            after = w + 2 if not _is_pci(w + 2) else w + 3
            print(f"\n  ⚠ B{pci:02d} is a PCI byte — [B{w:02d}:B{after:02d}] would include it!")
            print(f"    Use (B{w:02d} << 8) | B{after:02d} instead of [B{w:02d}:B{after:02d}]")
        if _is_pci(w - 1) and w > 0:
            pci = w - 1
            before = w - 2 if not _is_pci(w - 2) else w - 3
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


def _print_legend(sub_bytes: int, *, show_torque: bool = False, show_obdb: bool = False):
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
    print(_c(f"How a UDS response is counted {count} different ways", ansi.BOLD))
    print(
        "  A response rides on CAN frames (8 bytes each) → ISO-TP → UDS. Where you\n"
        "  start counting, and whether you include the transport framing, changes a\n"
        "  byte's index. canair expressions use the WiCAN index.\n"
    )
    print(_c("  Columns", ansi.BOLD))
    print(
        f"    {_pad('WiCAN', 8, ansi.CYAN)}  byte # in the raw CAN frames, framing INCLUDED (Bnn)"
    )
    print("    ISO-TP    byte # in the reassembled payload, framing stripped")
    if show_torque:
        print(f"    Torque    UDS data byte, letter notation ({variant} here: skips {sub_desc})")
        print(
            _c("              ↳ used by the Torque app, Car Scanner & similar OBD apps", ansi.DIM)
        )
    if show_obdb:
        print("    bix       OBDb bit index — the UDS data-byte index × 8")
    if show_torque or show_obdb:
        print(
            _c(
                "              ↳ Torque/OBDb count from the first UDS data byte, so the\n"
                "                mapping shifts with the subfunction width: use -1 for\n"
                "                21xx PIDs (Torque 1) and -2 for 22xxxx DIDs (Torque 2).",
                ansi.DIM,
            )
        )
    else:
        print(
            _c(
                "    (add --torque for the Torque letter column, --obdb for the OBDb bix column)",
                ansi.DIM,
            ),
        )
    print()
    print(_c("  Role — what a WiCAN row actually is", ansi.BOLD))
    print(f"    {_pad('FF PCI', 8, ansi.DIM)}  First-Frame framing (bytes B00–B01: type + length)")
    print(
        f"    {_pad('CF PCI', 8, ansi.DIM)}  Consecutive-Frame framing (1 byte at B08, B16, B24…)"
    )
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
            ansi.DIM,
        )
    )
    print()


def _print_table(
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
        _print_legend(sub_bytes, show_torque=show_torque, show_obdb=show_obdb)
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
            print(_c(f"|{label}|", ansi.CYAN))
            prev_frame = frame

        role = _table_role(w_idx, sub_bytes)
        line = _cells(
            f"B{w_idx:02d}",
            f"0x{row['isotp']:02X}" if row["isotp"] is not None else "",
            role,
            torque_c=row["torque_letter"] or "",
            bix_c=str(row["bix"]) if row["bix"] is not None else "",
        )
        # Dim the ISO-TP framing (PCI) rows so real data stands out.
        print(_c(line, ansi.DIM) if role.endswith("PCI") else line)


def _parse_hex_payload(raw: str) -> list[int]:
    """Parse a hex string (with or without spaces) into a list of byte values."""
    cleaned = raw.replace(" ", "").strip()
    if not cleaned:
        print("Error: --annotate requires a non-empty hex payload.", file=sys.stderr)
        sys.exit(1)
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
    bar = _cerr("⚠ WARNING", ansi.BOLD + ansi.YELLOW)
    rule = _cerr("  " + "─" * 68, ansi.YELLOW)
    print("", file=sys.stderr)
    print(f"  {bar}  {_cerr(headline, ansi.BOLD)}", file=sys.stderr)
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
            f"If this is a raw CAN frame straight off the bus, pass {_cerr('--raw', ansi.BOLD)}.",
        )
    elif raw_frame and _looks_like_uds_sid(first_byte):
        _emit_warning(
            f"first byte 0x{first_byte:02X} looks like a UDS response SID, not an ISO-TP PCI byte.",
            "--raw expects an already-framed CAN payload (PCI present).",
            f"If this is a PCI-stripped UDS payload, drop {_cerr('--raw', ansi.BOLD)}.",
        )


def _pid_sub_bytes(pid: str) -> int | None:
    """The subfunction width ``pid`` *conclusively* implies, or ``None``.

    :func:`canlib.notation.subfunction_bytes_for_pid` answers "2 for ``22xxxx``,
    else 1" — a **default**, not a determination. Its "else" branch swallows forms
    that say nothing about the service: the short DID form profiles also accept
    (``B004`` for ``22B004``) and standard OBD-II PIDs (``010C``) both read as
    1-byte. Deriving a width from that — or warning that an explicit ``-2``
    "contradicts" it — would state a layout with false confidence and, for a short
    DID, the wrong one. So only an explicit ``21``/``22`` service prefix counts as
    conclusive here; anything else keeps the plain 1-byte default, silently.

    The width itself still comes from the shared helper, so a conclusive PID can
    never be labelled differently here than by ``decode``/``hunt``/``coverage``.
    """
    if not pid.upper().startswith(("21", "22")):
        return None
    return subfunction_bytes_for_pid(pid)


def _resolve_sub_bytes(args) -> tuple[int, bool]:
    """Effective subfunction width and whether it was derived from ``--pid``.

    Precedence: an explicit ``-1``/``-2`` > the width ``--pid`` conclusively
    implies (:func:`_pid_sub_bytes`) > 1 (the ``21xx`` default). A ``--pid`` names
    the request whose echo is in the payload, so its service fixes the width — the
    derivation every other command does via
    :func:`canlib.notation.subfunction_bytes_for_pid`. Without it, ``--pid 22BC03``
    silently kept the 1-byte default and mislabelled the second DID echo byte as
    an unmapped data byte.
    """
    pid = getattr(args, "pid", None)
    explicit = args.sub_bytes
    if pid:
        implied = _pid_sub_bytes(pid)
        if implied is not None:
            if explicit is None:
                return implied, True
            _warn_sub_bytes_contradicts_pid(explicit, pid, implied)
    return (explicit if explicit is not None else 1), False


def _warn_sub_bytes_contradicts_pid(sub_bytes: int, pid: str, implied: int):
    """Warn when an explicit ``-1``/``-2`` disagrees with ``--pid``'s service.

    The explicit flag still wins (it is the override), but silently honouring
    ``-1 --pid 22BC03`` reproduces the very mislabelling the derivation fixes, so
    say so. Same spirit as :func:`_warn_payload_kind_mismatch`: the input
    contradicts the chosen mode. Only ever called for a *conclusive* ``--pid``
    (see :func:`_pid_sub_bytes`) — warning off the 1-byte default would tell users
    to drop a correct ``-2`` from a short-form DID.
    """
    if implied == sub_bytes:
        return
    kind = "2-byte DID" if implied == 2 else "1-byte PID"
    _emit_warning(
        f"-{sub_bytes} contradicts --pid {pid.upper()}, which is a {kind} subfunction.",
        f"Honouring the explicit -{sub_bytes}; the header/Param columns will be off by one",
        f"if that is not what you meant. Drop it (or pass {_cerr(f'-{implied}', ansi.BOLD)}) "
        "to use the PID's own width.",
    )


def _frame_sid(frame: list[tuple[int, int | None]]) -> int | None:
    """The response SID of a reconstructed WiCAN frame (ISO-TP payload byte 0)."""
    return next((value for value, pi in frame if pi == 0), None)


def _resolve_annotate_layout(
    frame: list[tuple[int, int | None]], explicit: int | None, pid: str | None
) -> tuple[int, ResponseLayout | None, str | None]:
    """Resolve ``(sub_bytes, layout, derived_from)`` for an annotate run.

    Evidence order, strongest first:

    1. the payload's own **response SID** — it names the service, which fixes the
       whole header shape (``0x62`` → ``DID``, ``0x71`` → ``SF`` + ``RID``, ``0x7F``
       → rejected SID + NRC). Nothing a flag or a PID string says is better
       evidence than the bytes in hand.
    2. a conclusive ``--pid`` (see :func:`_pid_sub_bytes`) — a width only.
    3. the 1-byte default.

    An explicit ``-1``/``-2`` overrides all of it. When it *agrees* with the
    recognised layout the rich per-field roles are kept; when it disagrees the
    layout is dropped (its roles imply its own width, so mixing the two would
    label bytes by a shape the user just rejected) and the generic ``SID`` +
    ``PID``/``DID`` labelling is used instead, with a warning.
    """
    sid = _frame_sid(frame)
    layout = response_layout(sid) if sid is not None else None
    # Carry the conclusive PID and its width together, so the non-optional name is
    # available wherever the width is (and neither can be used without the other).
    from_pid: tuple[str, int] | None = None
    if pid:
        width = _pid_sub_bytes(pid)
        if width is not None:
            from_pid = (pid, width)
    if layout is not None:
        if explicit is None:
            return layout.subfunction_bytes, layout, f"SID 0x{sid:02X}"
        if explicit == layout.subfunction_bytes:
            return explicit, layout, None
        _warn_sub_bytes_contradicts_sid(explicit, layout)
        return explicit, None, None
    if explicit is not None:
        # No recognisable service, so --pid is the only other evidence a
        # contradicting override can be checked against.
        if from_pid is not None:
            _warn_sub_bytes_contradicts_pid(explicit, from_pid[0], from_pid[1])
        return explicit, None, None
    if from_pid is not None:
        return from_pid[1], None, f"--pid {from_pid[0].upper()}"
    return 1, None, None


def _warn_sub_bytes_contradicts_sid(sub_bytes: int, layout: ResponseLayout):
    """Warn when an explicit ``-1``/``-2`` disagrees with the payload's own service."""
    name = service_response_name(layout.resp_sid) or "unknown service"
    shape = " + ".join(f.role if f.width == 1 else f"{f.role}({f.width}B)" for f in layout.fields)
    _emit_warning(
        f"-{sub_bytes} contradicts the payload: SID 0x{layout.resp_sid:02X} is {name},",
        f"whose header is SID + {shape} ({layout.subfunction_bytes} byte(s) after the SID).",
        f"Honouring the explicit -{sub_bytes} and falling back to generic role labels; "
        f"drop it to use the service's own layout.",
    )


def _generic_role(isotp_idx: int, sub_bytes: int) -> str:
    """Role of a payload byte with no recognised service: SID then PID/DID by width."""
    if isotp_idx == 0:
        return ROLE_SID
    if isotp_idx < 1 + sub_bytes:
        return ROLE_DID if sub_bytes == 2 else ROLE_PID
    return ""


def _print_service_line(layout: ResponseLayout, frame: list[tuple[int, int | None]]):
    """One dim line naming the service the payload came from (and, if negative, why)."""
    name = service_response_name(layout.resp_sid) or "unknown service"
    if layout.negative:
        by_idx = {pi: value for value, pi in frame if pi is not None}
        rejected, nrc = by_idx.get(1), by_idx.get(2)
        detail = ""
        if rejected is not None:
            rej_name = service_response_name(rejected) or f"service 0x{rejected:02X}"
            detail = f" rejecting 0x{rejected:02X} {rej_name}"
        if nrc is not None:
            detail += f" — NRC 0x{nrc:02X} {nrc_name(nrc)}"
        print(_c(f"  {name}{detail}", ansi.DIM))
        return
    shape = " + ".join(f"{f.role}×{f.width}" if f.width > 1 else f.role for f in layout.fields)
    header = f"SID + {shape}" if shape else "SID only"
    print(_c(f"  service: 0x{layout.resp_sid:02X} {name} — header {header}", ansi.DIM))


def _print_role_legend(roles: list[str], *, has_data: bool):
    """The trailing definition list: only the Role terms this payload actually used."""
    defs = role_definitions(roles)
    if not defs and not has_data:
        return
    blank = "(blank)"
    # Size the term column to every label rendered, "(blank)" included, so the
    # definitions line up as one block whatever roles the payload used.
    width = max([len(r) for r, _ in defs] + [len(blank) if has_data else 0])
    print()
    print(_c("  Roles in this payload", ansi.BOLD))
    for role, help_ in defs:
        print(f"    {_pad(role, width, ansi.CYAN)}  {help_}")
    if has_data:
        print(f"    {blank:<{width}}  data — the bytes an expression reads")


def _annotate_payload(
    payload_hex: str,
    explicit_sub_bytes: int | None,
    params: dict | None = None,
    *,
    raw_frame: bool = False,
    show_torque: bool = False,
    show_obdb: bool = False,
    pid: str | None = None,
    legend: bool = True,
) -> int:
    """Annotate each byte of a UDS response payload with WiCAN Bnn indices.

    By default ``payload_hex`` is the **reassembled UDS response payload**
    (SID-first, ISO-TP PCI bytes stripped — what the transport/captures hand
    back); the framing is reconstructed to show the WiCAN indices. With
    ``raw_frame=True`` the hex is an **already-framed CAN payload** (PCI present,
    e.g. copied straight off the bus) and is indexed as-is.

    The Torque letter and OBDb bix columns are distinct notations, each shown
    only when its flag (``show_torque`` / ``show_obdb``) is set — WiCAN and
    ISO-TP are the notations canair expressions use.

    **Roles come from the payload's own service.** The response SID is looked up in
    :mod:`canlib.uds_layout`, so each header byte is named for what it actually is
    (``DID`` / ``LID`` / ``RID`` / ``SF`` / ``CTRL``, or a negative response's
    rejected SID + ``NRC``) instead of being guessed from a 1-vs-2-byte width.
    ``explicit_sub_bytes`` (``-1``/``-2``) overrides that and ``pid`` is the weaker
    fallback — see :func:`_resolve_annotate_layout`. The chosen width is captioned
    so it is never a silent choice, and with ``legend`` a definition list of the
    roles this payload used is printed underneath.

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

    sub_bytes, layout, derived_from = _resolve_annotate_layout(frame, explicit_sub_bytes, pid)
    overlay = params is not None
    mapped = mapped_offsets(params) if overlay else {}
    mbits = mapped_bits(params) if overlay else {}

    if layout is not None:
        _print_service_line(layout, frame)
    elif derived_from:
        kind = "2-byte DID" if sub_bytes == 2 else "1-byte PID"
        print(
            _c(
                f"  subfunction: {kind} — derived from {derived_from} (override with -1/-2)",
                ansi.DIM,
            )
        )

    if show_torque or show_obdb:
        # The Torque/OBDb numbering isn't fixed — it counts from the first UDS
        # *data* byte, so it shifts with the header size. For a plain identifier
        # read that is the familiar Torque 1 (21xx) / Torque 2 (22xxxx) split; a
        # richer header (e.g. 0x71's SF + RID) simply skips more bytes, so describe
        # the header generically and only offer the -1/-2 hint where it applies.
        if layout is not None:
            skipped = "SID + " + " + ".join(
                f.role if f.width == 1 else f"{f.width} {f.role} bytes" for f in layout.fields
            )
        else:
            skipped = "SID + 2 DID bytes" if sub_bytes == 2 else "SID + 1 PID byte"
        variant = f"Torque {sub_bytes}" if sub_bytes in (1, 2) else f"Torque (sub={sub_bytes})"
        hint = f" pass -{3 - sub_bytes} for the other variant." if sub_bytes in (1, 2) else ""
        if show_torque:
            cols = "Torque + bix columns" if show_obdb else "Torque column"
            caption = f"  {cols} = {variant} (skips {skipped});{hint}"
        else:  # bix only — describe it without leaning on the Torque label
            caption = (
                f"  bix column = OBDb bit index from the first UDS data byte "
                f"(skips {skipped});{hint}"
            )
        print(_c(caption.rstrip(";").rstrip(), ansi.DIM))

    # Per-byte roles come from the payload's own service when it is recognised, so
    # every header field is named (SF/RID/CTRL/NRC/…); otherwise fall back to the
    # width-based SID + PID/DID labelling. Computed up front so the Role column can
    # be sized to the widest label actually rendered.
    roles = [
        ROLE_PCI
        if pi is None
        else (layout.role_at(pi) or "" if layout is not None else _generic_role(pi, sub_bytes))
        for _, pi in frame
    ]

    # Column widths, shared by header / separator / data rows so everything lines
    # up (the Role cell must be padded to width, else the trailing Param divider
    # floats). Torque is a letter (A, AB…), bix up to 3 digits. Role stays at 6 for
    # the common labels and only widens for a longer one ("REJ SID"), so existing
    # output is byte-identical. Torque and bix are independent opt-ins.
    W_WICAN, W_HEX, W_ISOTP, W_TORQUE, W_BIX = 5, 4, 6, 6, 5
    W_ROLE = max(6, *(len(r) for r in roles)) if roles else 6

    def _row(wican, hex_, isotp, role, param=None, *, torque=None, bix=None):
        line = f"  {wican:>{W_WICAN}} | {hex_:>{W_HEX}} | {isotp:>{W_ISOTP}} | "
        if show_torque:
            line += f"{torque:>{W_TORQUE}} | "
        if show_obdb:
            line += f"{bix:>{W_BIX}} | "
        line += f"{role:<{W_ROLE}}"
        if param is not None:
            line += f" | {param}"
        return line.rstrip()

    def _seg():
        parts = [f"{'─' * W_WICAN}─", f"─{'─' * W_HEX}─", f"─{'─' * W_ISOTP}─"]
        if show_torque:
            parts.append(f"─{'─' * W_TORQUE}─")
        if show_obdb:
            parts.append(f"─{'─' * W_BIX}─")
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
            print(_c(label, ansi.CYAN))
            prev_frame = cur_frame

        # Derive every notation from the byte's ACTUAL ISO-TP index (pi) in the
        # reconstructed frame, so single-frame (1 PCI byte) and multi-frame
        # (2 FF + 1/CF PCI bytes) payloads are both correct. The length-agnostic
        # wican_to_isotp() assumes the multi-frame layout and is off-by-one for
        # single-frame responses — using pi keeps this column in step with Role.
        isotp = pi
        # Torque/bix are only rendered when their column is enabled; skip the
        # work otherwise (and avoid computing the letter past the ZZ boundary).
        if show_torque or show_obdb:
            torque = isotp_to_torque(pi, sub_bytes) if pi is not None else None
            bix = torque_to_bix(torque) if torque is not None else None
            letter = torque_label(torque)
        else:
            torque = bix = letter = None

        role = roles[w]
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
        print(_c(line, ansi.DIM) if role == ROLE_PCI else line)

    if legend:
        _print_role_legend(roles, has_data=any(r == "" for r in roles))
    return 0


def _param_cell(offset: int, byte_val: int, role: str, mapped: dict, mbits: dict) -> str:
    """The Param-overlay cell for one byte: covering param(s), or 'unmapped'.

    Any non-empty ``role`` is ISO-TP framing or a UDS header field, which a
    parameter can never read — an empty role is the only thing that means "data",
    so new roles (``SF``/``RID``/``CTRL``/``NRC``/…) are excluded automatically.
    """
    if role:
        return ""
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


def _print_overview(sub_bytes: int, *, show_torque: bool = False, show_obdb: bool = False):
    """The friendly bare-``bix`` landing view: legend + a compact 2-frame table.

    Shows just the first two CAN frames (B00–B15) — enough to see the FF/CF PCI
    boundary — and points at the ways to go deeper.
    """
    _print_table(sub_bytes, max_wican=15, legend=True, show_torque=show_torque, show_obdb=show_obdb)
    print()
    print(_c("  Go further", ansi.BOLD))
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
        _c("    canair bix -2 …             same, for 22xxxx DIDs (2-byte subfunction)", ansi.DIM)
    )


def run(args) -> int:
    # --annotate resolves its own width from the payload (the strongest evidence),
    # so _resolve_sub_bytes is only for the payload-less views below.
    if args.table:
        sub_bytes, _ = _resolve_sub_bytes(args)
        _print_table(
            sub_bytes,
            args.max,
            legend=True,
            show_torque=args.show_torque,
            show_obdb=args.show_obdb,
        )
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
            show_obdb=args.show_obdb,
            pid=args.pid,
            legend=args.legend,
        )

    if args.raw_frame:
        print("Error: --raw only applies to --annotate.", file=sys.stderr)
        return 1

    if not args.value:
        sub_bytes, _ = _resolve_sub_bytes(args)
        _print_overview(sub_bytes, show_torque=args.show_torque, show_obdb=args.show_obdb)
        return 0

    notation, idx = _parse_input(args.value)
    sub_bytes, _ = _resolve_sub_bytes(args)
    _print_result(notation, idx, sub_bytes)
    return 0
