"""Converting one index to every notation."""

from __future__ import annotations

import re

from canair.render import messages
from canair.uds.byteindex import (
    bix_to_wican,
    isotp_to_wican,
    letter_to_torque_idx,
    torque_label,
    torque_to_wican,
    wican_to_bix,
    wican_to_isotp,
    wican_to_torque,
)


def parse_input(value: str) -> tuple[str, int]:
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

    messages.fail(f"cannot parse '{value}'. Use w9, B9, i6, b32, E, AA, or a plain number.", code=2)


def print_result(notation: str, idx: int, sub_bytes: int):
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
