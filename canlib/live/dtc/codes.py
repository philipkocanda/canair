"""DTC codes: SAE J2012 formatting, statusOfDTC bits, and record decoding.

Pure (no I/O). UDS records are 4 bytes (3-byte DTC + status); KWP2000 records are
3 bytes (2-byte DTC + status), whose status bits are not the UDS layout.
"""

from __future__ import annotations

from canlib.uds.protocol import KWP, UDS

# SAE J2012 DTC category letters, selected by the top two bits of the first byte.
_DTC_LETTERS = ("P", "C", "B", "U")

# statusOfDTC bit meanings (ISO 14229-1 / UDS), bit 0 = LSB.
_STATUS_BITS = (
    "testFailed",
    "testFailedThisOperationCycle",
    "pendingDTC",
    "confirmedDTC",
    "testNotCompletedSinceLastClear",
    "testFailedSinceLastClear",
    "testNotCompletedThisOperationCycle",
    "warningIndicatorRequested",
)


def format_dtc(b0: int, b1: int, b2: int) -> str:
    """Format a 3-byte UDS DTC as ``Lxxxx-yy`` (SAE J2012 + failure type)."""
    letter = _DTC_LETTERS[(b0 >> 6) & 0x03]
    d1 = (b0 >> 4) & 0x03
    d2 = b0 & 0x0F
    return f"{letter}{d1}{d2:X}{b1:02X}-{b2:02X}"


def format_kwp_dtc(b0: int, b1: int) -> str:
    """Format a 2-byte KWP2000 DTC as ``Lxxxx`` (no failure-type byte)."""
    letter = _DTC_LETTERS[(b0 >> 6) & 0x03]
    d1 = (b0 >> 4) & 0x03
    d2 = b0 & 0x0F
    return f"{letter}{d1}{d2:X}{b1:02X}"


def decode_status(status: int) -> list[str]:
    """Return the names of the set UDS statusOfDTC bits, LSB first."""
    return [name for bit, name in enumerate(_STATUS_BITS) if status & (1 << bit)]


def decode_dtc_records(data: bytes) -> list[dict]:
    """Decode UDS 4-byte DTC records (3-byte DTC + 1-byte status)."""
    records = []
    for i in range(0, len(data) - 3, 4):
        b0, b1, b2, status = data[i], data[i + 1], data[i + 2], data[i + 3]
        records.append(
            {
                "dtc": format_dtc(b0, b1, b2),
                "raw": f"{b0:02X}{b1:02X}{b2:02X}",
                "status": status,
                "status_bits": decode_status(status),
            }
        )
    return records


def decode_kwp_dtc_records(data: bytes) -> list[dict]:
    """Decode KWP2000 3-byte DTC records (2-byte DTC + 1-byte status).

    KWP2000 statusOfDTC bit semantics are not the UDS layout, so the raw status
    byte is reported without named bits.
    """
    records = []
    for i in range(0, len(data) - 2, 3):
        b0, b1, status = data[i], data[i + 1], data[i + 2]
        records.append(
            {
                "dtc": format_kwp_dtc(b0, b1),
                "raw": f"{b0:02X}{b1:02X}",
                "status": status,
                "status_bits": [],
            }
        )
    return records


def resolve_protocol(protocol: str, tx_id: int) -> str:
    """Resolve ``auto`` to ``uds``/``kwp`` from the ECU's id_protocol registry."""
    if protocol in (UDS, KWP):
        return protocol
    from ...ecus import ecu_protocol

    return KWP if ecu_protocol(tx_id) == KWP else UDS
