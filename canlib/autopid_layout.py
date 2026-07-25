"""Reconstruct the WiCAN AutoPID byte layout from a reassembled UDS payload.

This is an **analysis/decoding** concern, not device management: byte-index
expressions (``B09``, ``B37``, …) reference the interleaved AutoPID layout (with
PCI bytes), but every transport hands us the reassembled UDS payload *without*
PCI. This module re-inserts the PCI bytes so those expressions evaluate
correctly, whether the payload came live from a device or from a stored capture.
Transport-independent and usable offline.

The PCI-insertion math itself lives in :mod:`canlib.byteindex`
(:func:`~canlib.byteindex.payload_to_wican_bytes`), the single source of truth
shared with ``bix``/``coverage``/the analysis engine. This module is a thin
adapter over it that additionally re-applies the multi-frame **zero-padding** of
the final consecutive frame — matching the on-wire firmware buffer, where every
CAN frame carries a full 8 data bytes.
"""


def uds_hex_to_wican_bytes(hex_str: str) -> bytes:
    """Convert a reassembled UDS payload to the WiCAN AutoPID byte layout.

    WiCAN AutoPID runs with ELM327 headers ON and spaces ON. Its
    parse_elm327_response() copies ALL 8 CAN data bytes from each frame
    (including PCI bytes) sequentially into response.data, so byte indices in
    expressions (``B09``, ``B37``, …) reference that interleaved layout:
    ``B0=PCI``, ``B1=length_lo``, ``B2=SID``, ``B8=PCI_CF1``,
    ``B9=first_data_byte_CF1``, …

    Delegates the PCI insertion to
    :func:`canlib.byteindex.payload_to_wican_bytes` (the shared implementation),
    then zero-pads the final consecutive frame out to a full 8-byte CAN frame for
    multi-frame responses — the padding the firmware buffer carries but the
    PCI-stripped payload does not. Single-frame responses (≤7 UDS bytes) are not
    padded.
    """
    from .byteindex import payload_to_wican_bytes

    wican = bytearray(payload_to_wican_bytes(hex_str))
    payload_len = len(hex_str.replace(" ", "")) // 2
    if payload_len > 7:
        # Multi-frame: pad the final consecutive frame to a full 8-byte CAN frame.
        wican.extend(b"\x00" * ((-len(wican)) % 8))
    return bytes(wican)
