"""Reading a profile's broadcast signal maps (``signals/<bus>.yaml``).

The read counterpart of :mod:`canlib.signals_edit`, mirroring the
``states``/``states_edit`` and ``ecu_groups``/``groups_edit`` split: one loader
that resolves the profile's signal directory and parses its per-bus documents, so
`signals list`, `export dbc` and `validate signals` all see the same thing.

A signal map is domain B (passively-broadcast frames): arbitration ID → named
linear signals. Distinct from a PID's freeform WiCAN ``expression`` params.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .profile import Profile


@dataclass(frozen=True)
class SignalDoc:
    """One ``signals/<bus>.yaml`` document, as read from disk."""

    bus: str  # the file stem — the CAN bus code the map belongs to
    path: Path
    data: dict


def signals_dir(profile: Profile | None = None) -> Path:
    """The profile's signal-map directory (may not exist — signals/ is optional)."""
    from .profile import active

    return (profile or active()).signals_dir


def signal_byte_span(start_bit: int, length: int, byte_order: str = "little") -> int:
    """How many data bytes a signal occupies, counted from byte 0.

    ``start_bit`` uses DBC numbering: the LSB for little-endian, but the *MSB* for
    big-endian, whose bits then run down each byte and on into the next one (bit 7 of
    byte 0 is 7, so a 16-bit field in bytes 0-1 is ``7|16@0``). ``start_bit + length``
    is therefore only the extent of a little-endian field.
    """
    if byte_order == "big":
        last = (start_bit // 8) * 8 + (7 - start_bit % 8) + length - 1
    else:
        last = start_bit + length - 1
    return last // 8 + 1


def load_signals(bus: str | None = None, *, profile: Profile | None = None) -> list[SignalDoc]:
    """Every signal map in the profile, bus-sorted; one bus when ``bus`` is given.

    Returns ``[]`` when ``signals/`` is absent or empty — callers that need to
    tell those apart (to explain themselves to the user) can check
    ``signals_dir(...).is_dir()``.
    """
    from canlib import yaml_io

    directory = signals_dir(profile)
    if not directory.is_dir():
        return []
    docs: list[SignalDoc] = []
    for path in sorted(directory.glob("*.yaml")):
        if bus is not None and path.stem != bus:
            continue
        docs.append(SignalDoc(path.stem, path, yaml_io.safe_load(path.read_text()) or {}))
    return docs
