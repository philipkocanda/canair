"""A one-line human description of a UDS response.

Pure and device-free: bytes in, text (or ``None`` for a response it has no words for) out.
"""

from __future__ import annotations

from canair.uds.obd import is_support_bitmap_pid, supported_pids
from canair.uds.subfunctions import subfunction_name


def decode_uds_response(data: bytes) -> str | None:
    """Return a human-readable one-line decode of a UDS response, or None."""
    if len(data) < 1:
        return None

    sid = data[0]

    def _sub(request_sid: int, value: int) -> str:
        """Name a sub-function/control byte from the shared table, else hex."""
        return subfunction_name(request_sid, value) or f"0x{value:02X}"

    # 0x50-0x5F: DiagnosticSessionControl, ECUReset, SecurityAccess, etc.
    if sid == 0x50 and len(data) >= 2:
        return f"DiagnosticSessionControl: {_sub(0x10, data[1])}"

    if sid == 0x51 and len(data) >= 2:
        return f"ECUReset: {_sub(0x11, data[1])}"

    if sid == 0x67 and len(data) >= 2:
        level = data[1]
        if level % 2 == 1:  # odd = seed response
            seed_hex = data[2:].hex().upper()
            return f"SecurityAccess: level {level} seed = {seed_hex}"
        else:
            return f"SecurityAccess: level {level} key accepted"

    if sid == 0x62 and len(data) >= 3:
        did = (data[1] << 8) | data[2]
        payload_len = len(data) - 3
        return f"ReadDataByIdentifier: DID 0x{did:04X}, {payload_len} data bytes"

    if sid == 0x41 and len(data) >= 2:
        pid = data[1]
        if is_support_bitmap_pid(pid) and len(data) == 6:
            listed = " ".join(f"{p:02X}" for p in supported_pids(pid, data[2:6])) or "none"
            return f"OBD-II mode 01: PID 0x{pid:02X} supported PIDs {listed}"
        return f"OBD-II mode 01: PID 0x{pid:02X}, {len(data) - 2} data bytes"

    if sid == 0x49 and len(data) >= 2:
        return f"OBD-II mode 09: InfoType 0x{data[1]:02X}, {len(data) - 2} data bytes"

    # 0x61 answers 0x21 ReadDataByLocalIdentifier (KWP2000), whose identifier is a
    # 1-byte Local Identifier — not the 2-byte DID of UDS 0x22. Named to match the
    # uds_services registry and the LID role canair reports elsewhere.
    if sid == 0x61 and len(data) >= 2:
        lid = data[1]
        payload_len = len(data) - 2
        return f"ReadDataByLocalIdentifier: LID 0x{lid:02X}, {payload_len} data bytes"

    if sid == 0x6E and len(data) >= 3:
        did = (data[1] << 8) | data[2]
        return f"WriteDataByIdentifier: DID 0x{did:04X} accepted"

    if sid == 0x6F and len(data) >= 3:
        did = (data[1] << 8) | data[2]
        ctrl_name = _sub(0x2F, data[3]) if len(data) >= 4 else "?"
        status = data[4:].hex().upper() if len(data) > 4 else ""
        result = f"IOControl: DID 0x{did:04X}, {ctrl_name}"
        if status:
            result += f", status={status}"
        return result

    if sid == 0x71 and len(data) >= 4:
        rid = (data[2] << 8) | data[3]
        return f"RoutineControl: {_sub(0x31, data[1])} routine 0x{rid:04X}"

    if sid == 0x59 and len(data) >= 2:
        name = subfunction_name(0x19, data[1]) or f"subFunction 0x{data[1]:02X}"
        return f"ReadDTCInformation: {name}, {len(data) - 2} data bytes"

    if sid == 0x63 and len(data) >= 2:
        addr_len = len(data) - 1
        return f"ReadMemoryByAddress: {addr_len} bytes returned"

    # 0x74/0x75 carry a *lengthFormatIdentifier*, not the request's
    # addressAndLengthFormatIdentifier: ISO 14229-1 defines its high nibble as the
    # byte-length of the maxNumberOfBlockLength that follows, and its low nibble as
    # RESERVED (0). Reporting that low nibble as an "addrLen" read request
    # semantics into a response; the useful datum is the block size itself.
    def _transfer_block_size(data: bytes) -> str:
        n = (data[1] >> 4) & 0xF
        block = data[2 : 2 + n]
        if n and len(block) == n:
            return f"max block {int.from_bytes(block, 'big')} bytes"
        return f"max-block length field {n} byte(s), value truncated"

    if sid == 0x74 and len(data) >= 2:
        return (
            "WARNING: RequestDownload ACCEPTED — ECU ready to receive firmware "
            f"({_transfer_block_size(data)})"
        )

    if sid == 0x75 and len(data) >= 2:
        return (
            "WARNING: RequestUpload ACCEPTED — ECU ready to send memory "
            f"({_transfer_block_size(data)})"
        )

    if sid == 0x76 and len(data) >= 2:
        seq = data[1]
        payload_len = len(data) - 2
        return f"WARNING: TransferData — block {seq}, {payload_len} bytes being transferred"

    if sid == 0x77:
        return "WARNING: TransferExit — firmware transfer completed"

    # 0x68 is the response to 0x28 CommunicationControl (request SID + 0x40),
    # which carries a controlType — not an identifier.
    if sid == 0x68 and len(data) >= 2:
        return f"CommunicationControl: {_sub(0x28, data[1])}"

    # 0x6C is the response to 0x2C DynamicallyDefineDataIdentifier. The define
    # variants echo the 2-byte dynamic DID they defined; a clear may omit it.
    if sid == 0x6C and len(data) >= 2:
        name = subfunction_name(0x2C, data[1]) or f"subFunction 0x{data[1]:02X}"
        result = f"DynamicallyDefineDataIdentifier: {name}"
        if len(data) >= 4:
            did = (data[2] << 8) | data[3]
            result += f", dynamic DID 0x{did:04X}"
        return result

    # 0xC5 is the response to 0x85 ControlDTCSetting, which carries a
    # DTCSettingType — not a DID.
    if sid == 0xC5 and len(data) >= 2:
        return (
            f"WARNING: ControlDTCSetting — DTC setting {_sub(0x85, data[1])}, "
            "DTC logging may be altered"
        )

    if sid == 0x7E:
        return "TesterPresent: acknowledged"

    return None
