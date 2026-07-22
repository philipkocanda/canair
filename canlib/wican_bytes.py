"""Reconstruct the WiCAN AutoPID byte layout from a reassembled UDS payload.

Transport-independent and usable offline: byte-index expressions (``B09``,
``B37``, …) reference the interleaved AutoPID layout (with PCI bytes), but every
transport hands us the reassembled UDS payload *without* PCI. This module
re-inserts the PCI bytes so those expressions evaluate correctly, whether the
payload came live from a device or from a stored capture.
"""


def uds_hex_to_wican_bytes(hex_str: str) -> bytes:
    """Convert a reassembled UDS payload to the WiCAN AutoPID byte layout.

    WiCAN AutoPID runs with ELM327 headers ON and spaces ON. Its
    parse_elm327_response() copies ALL 8 CAN data bytes from each frame
    (including PCI bytes) sequentially into response.data. This means:

      Frame 0 (First Frame):  [10 LL] [SID SUB d d d d]  -> 8 bytes
      Frame 1 (Consecutive):  [21]    [d d d d d d d]    -> 8 bytes
      Frame 2 (Consecutive):  [22]    [d d d d d d d]    -> 8 bytes
      ...

    Byte indices in expressions (B09, B37, etc.) reference this interleaved
    format. B0=PCI, B1=length_lo, B2=SID, B8=PCI_CF1, B9=first_data_byte_CF1.

    Our transports return ONLY the reassembled UDS payload without PCI. We must
    reconstruct the AutoPID layout by re-inserting the PCI bytes at the correct
    positions.

    For single-frame responses (<=7 UDS bytes): PCI is 1 byte (0x0N).
    For multi-frame responses (>6 UDS bytes):
      - First frame PCI: 2 bytes (0x10 | (len>>8), len & 0xFF)
      - Consecutive frame PCI: 1 byte each (0x20 | (seq & 0x0F))
    """
    data = bytes.fromhex(hex_str)
    payload_len = len(data)

    if payload_len <= 7:
        return bytes([payload_len]) + data
    else:
        result = bytearray()
        pci_hi = 0x10 | ((payload_len >> 8) & 0x0F)
        pci_lo = payload_len & 0xFF
        result.extend([pci_hi, pci_lo])
        result.extend(data[:6])

        offset = 6
        seq = 1
        while offset < payload_len:
            pci_cf = 0x20 | (seq & 0x0F)
            result.append(pci_cf)
            chunk = data[offset : offset + 7]
            result.extend(chunk)
            if len(chunk) < 7:
                result.extend(b"\x00" * (7 - len(chunk)))
            offset += 7
            seq += 1

        return bytes(result)
