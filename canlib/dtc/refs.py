"""How the DTC log names an ECU: by its CAN response address, as a capture does.

An entry records the ECU's ``rx`` (``"0x7EC"``, :func:`ecu_ref`) and nothing else about it:
the name, the request id and the display label are looked up in the profile when the log
is read (:class:`EcuRefs`). A rename therefore needs no rewrite, and an ECU the profile no
longer knows still shows its address. The response address rather than the request id,
because under shared-header addressing several ECUs are asked on one request id but each
answers on its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


def ecu_ref(tx_id: int) -> str:
    """The reference the log records for the ECU asked on ``tx_id`` (its rx, as hex)."""
    from canlib.ecus import rx_addr_str

    return rx_addr_str(tx_id)


def _parse(ref: object) -> int | None:
    try:
        return int(str(ref), 16)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class EcuRefs:
    """The active profile's view of logged ECU references (build once per read)."""

    tx_by_rx: Mapping[int, int] = field(default_factory=dict)
    names: Mapping[int, str] = field(default_factory=dict)

    @classmethod
    def current(cls) -> EcuRefs:
        """From the active profile's registry, or empty when none resolves."""
        from canlib.ecus import build_rx_index, build_rx_tx_index, load_ecus

        try:
            ecus = load_ecus()
        except Exception:
            return cls()
        return cls(build_rx_tx_index(ecus), build_rx_index(ecus))

    def name(self, ref: object) -> str | None:
        """The ECU's registry name, or None when the profile doesn't know the address."""
        rx = _parse(ref)
        return self.names.get(rx) if rx is not None else None

    def label(self, ref: object) -> str:
        """``BMS (0x7E4)`` — the name and request id people use — or the bare address."""
        rx = _parse(ref)
        tx = self.tx_by_rx.get(rx) if rx is not None else None
        name = self.names.get(rx) if rx is not None else None
        if tx is None or name is None:
            return str(ref)
        return f"{name} (0x{tx:03X})"
