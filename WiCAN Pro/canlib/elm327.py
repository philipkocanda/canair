"""ELM327 response parsing, byte conversion, and command safety checks."""

import re

# UDS Negative Response Code descriptions
NRC_CODES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x14: "responseTooLong",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x25: "noResponseFromSubnetComponent",
    0x26: "failurePreventsExecutionOfRequestedAction",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceededNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x70: "uploadDownloadNotAccepted",
    0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure",
    0x73: "wrongBlockSequenceCounter",
    0x78: "requestCorrectlyReceivedResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}

# UDS services that can write to ECU memory, reflash firmware, or actuate
# physical outputs. Blocked by default to prevent accidental damage.
BLOCKED_UDS_SERVICES = {
    0x2E: "WriteDataByIdentifier (write to ECU memory)",
    0x34: "RequestDownload (ECU reflash)",
    0x35: "RequestUpload (ECU memory dump)",
    0x36: "TransferData (flash data transfer)",
    0x37: "RequestTransferExit (finalize flash)",
    0x38: "RequestFileTransfer",
}


def check_command_safety(cmd: str) -> str | None:
    """Check if a command is potentially dangerous.

    Returns an error message if the command is blocked, or None if safe.
    Checks both raw UDS hex commands and AT commands.
    """
    clean = cmd.strip().upper()

    if clean.startswith("AT"):
        return None

    hex_only = clean.replace(" ", "")
    if not hex_only or not all(c in "0123456789ABCDEF" for c in hex_only):
        return None

    if len(hex_only) < 2:
        return None

    service_byte = int(hex_only[:2], 16)

    if service_byte in BLOCKED_UDS_SERVICES:
        desc = BLOCKED_UDS_SERVICES[service_byte]
        return f"BLOCKED: UDS service 0x{service_byte:02X} -- {desc}"

    if service_byte == 0x10 and len(hex_only) >= 4:
        sub = int(hex_only[2:4], 16)
        if sub == 0x02:
            return ("BLOCKED: DiagnosticSessionControl sub 0x02 "
                    "(programmingSession) -- required for flash/write operations")

    return None


def parse_elm_response(raw: str) -> dict:
    """Parse an ELM327 response into structured data.

    Returns dict with keys:
        ok: bool - whether a positive response was received
        hex: str - raw hex string of response data (if ok)
        bytes: bytes - parsed response bytes (if ok)
        nrc: int - negative response code (if not ok)
        nrc_desc: str - NRC description (if not ok)
        error: str - error message (if parse failed)
        raw: str - original response text
    """
    result = {"raw": raw, "ok": False}

    lines = raw.strip().split("\n")
    lines = [l.strip() for l in lines if l.strip()]

    data_lines = []
    for line in lines:
        line = line.rstrip(">").strip()
        if not line:
            continue
        if line.startswith("AT") or line.startswith("at"):
            continue
        if line == "OK":
            continue
        # Filter out ISO-TP flow control frame echoes
        fc_check = line.replace(" ", "").upper()
        if len(fc_check) >= 6 and fc_check[:3].isalnum():
            fc_body = fc_check[3:]
        else:
            fc_body = fc_check
        if fc_body.startswith("F0") and len(fc_body) <= 8:
            continue
        if line == "?":
            result["error"] = "Unknown command"
            return result
        if line == "NO DATA":
            result["error"] = "No response from ECU (NO DATA)"
            return result
        if line == "CAN ERROR":
            result["error"] = "CAN bus error"
            return result
        if line == "UNABLE TO CONNECT":
            result["error"] = "Unable to connect to CAN bus"
            return result
        if line == "BUS INIT: ...ERROR":
            result["error"] = "Bus initialization error"
            return result
        if line == "STOPPED":
            result["error"] = "Request stopped"
            return result
        if line == "BUFFER FULL":
            result["error"] = "Response buffer full"
            return result
        data_lines.append(line)

    if not data_lines:
        result["error"] = "Empty response"
        return result

    # Filter out echo of the request
    if len(data_lines) > 1:
        first = data_lines[0].replace(" ", "")
        if len(first) >= 2 and all(c in "0123456789ABCDEFabcdef" for c in first):
            first_byte = int(first[:2], 16)
            if 0x10 <= first_byte <= 0x3E:
                data_lines = data_lines[1:]

    # Check for multi-frame ISO-TP format
    is_multiframe = any(re.match(r"^\d+:", line) for line in data_lines)

    if is_multiframe:
        frame_lines = []
        for line in data_lines:
            m = re.match(r"^(\d+):([0-9A-Fa-f]+)$", line)
            if m:
                frame_lines.append((int(m.group(1)), m.group(2)))
        frame_lines.sort(key=lambda x: x[0])
        hex_clean = "".join(hex_data for _, hex_data in frame_lines)
    else:
        hex_str = " ".join(data_lines)
        hex_clean = hex_str.replace(" ", "")

    if not all(c in "0123456789ABCDEFabcdef" for c in hex_clean):
        result["error"] = f"Non-hex response: {hex_clean[:80]}"
        return result

    if len(hex_clean) < 2:
        result["error"] = f"Response too short: {hex_clean}"
        return result

    try:
        response_bytes = bytes.fromhex(hex_clean)
    except ValueError as e:
        result["error"] = f"Hex decode failed: {e}"
        return result

    result["hex"] = hex_clean.upper()
    result["bytes"] = response_bytes

    if response_bytes[0] == 0x7F and len(response_bytes) >= 3:
        nrc = response_bytes[2]
        result["nrc"] = nrc
        result["nrc_service"] = response_bytes[1]
        result["nrc_desc"] = NRC_CODES.get(nrc, f"unknown (0x{nrc:02X})")
        return result

    result["ok"] = True
    return result


def elm_hex_to_wican_bytes(hex_str: str) -> bytes:
    """Convert ELM327 reassembled payload to WiCAN AutoPID byte layout.

    WiCAN AutoPID runs with ELM327 headers ON and spaces ON. Its
    parse_elm327_response() copies ALL 8 CAN data bytes from each frame
    (including PCI bytes) sequentially into response.data. This means:

      Frame 0 (First Frame):  [10 LL] [SID SUB d d d d]  -> 8 bytes
      Frame 1 (Consecutive):  [21]    [d d d d d d d]    -> 8 bytes
      Frame 2 (Consecutive):  [22]    [d d d d d d d]    -> 8 bytes
      ...

    Byte indices in expressions (B09, B37, etc.) reference this interleaved
    format. B0=PCI, B1=length_lo, B2=SID, B8=PCI_CF1, B9=first_data_byte_CF1.

    The ELM327 terminal (our transport) returns ONLY the reassembled UDS
    payload without PCI. We must reconstruct the AutoPID layout by
    re-inserting the PCI bytes at the correct positions.

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
            chunk = data[offset:offset + 7]
            result.extend(chunk)
            if len(chunk) < 7:
                result.extend(b'\x00' * (7 - len(chunk)))
            offset += 7
            seq += 1

        return bytes(result)
