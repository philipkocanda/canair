"""A one-line human description of a UDS response.

Pure and device-free: bytes in, text (or ``None`` for a response it has no words for) out.
Each positive response the module can describe has one describer, looked up by the reply's
first byte, and the table names that byte by its service, not by its value.
"""

from __future__ import annotations

from collections.abc import Callable

from canair.uds.obd import is_support_bitmap_pid, supported_pids
from canair.uds.services import (
    COMMUNICATION_CONTROL,
    CONTROL_DTC_SETTING,
    DIAGNOSTIC_SESSION_CONTROL,
    DYNAMICALLY_DEFINE_DATA_IDENTIFIER,
    ECU_RESET,
    INPUT_OUTPUT_CONTROL_BY_IDENTIFIER,
    KWP_READ_DATA_BY_LOCAL_IDENTIFIER,
    OBD_REQUEST_VEHICLE_INFORMATION,
    OBD_SHOW_CURRENT_DATA,
    READ_DATA_BY_IDENTIFIER,
    READ_DTC_INFORMATION,
    READ_MEMORY_BY_ADDRESS,
    REQUEST_DOWNLOAD,
    REQUEST_TRANSFER_EXIT,
    REQUEST_UPLOAD,
    ROUTINE_CONTROL,
    SECURITY_ACCESS,
    TESTER_PRESENT,
    TRANSFER_DATA,
    WRITE_DATA_BY_IDENTIFIER,
    positive_response_sid,
)
from canair.uds.subfunctions import subfunction_name


def _sub(request_sid: int, value: int) -> str:
    """Name a sub-function/control byte from the shared table, else hex."""
    return subfunction_name(request_sid, value) or f"0x{value:02X}"


def _word(data: bytes, at: int) -> int:
    return (data[at] << 8) | data[at + 1]


def _session(data: bytes) -> str:
    return f"DiagnosticSessionControl: {_sub(DIAGNOSTIC_SESSION_CONTROL, data[1])}"


def _reset(data: bytes) -> str:
    return f"ECUReset: {_sub(ECU_RESET, data[1])}"


def _security_access(data: bytes) -> str:
    level = data[1]
    if level % 2 == 1:  # odd = seed response
        return f"SecurityAccess: level {level} seed = {data[2:].hex().upper()}"
    return f"SecurityAccess: level {level} key accepted"


def _read_did(data: bytes) -> str:
    return f"ReadDataByIdentifier: DID 0x{_word(data, 1):04X}, {len(data) - 3} data bytes"


def _obd_current_data(data: bytes) -> str:
    pid = data[1]
    if is_support_bitmap_pid(pid) and len(data) == 6:
        listed = " ".join(f"{p:02X}" for p in supported_pids(pid, data[2:6])) or "none"
        return f"OBD-II mode 01: PID 0x{pid:02X} supported PIDs {listed}"
    return f"OBD-II mode 01: PID 0x{pid:02X}, {len(data) - 2} data bytes"


def _obd_vehicle_information(data: bytes) -> str:
    return f"OBD-II mode 09: InfoType 0x{data[1]:02X}, {len(data) - 2} data bytes"


def _read_local_id(data: bytes) -> str:
    # KWP2000 0x21's identifier is a 1-byte Local Identifier, not the 2-byte DID of UDS
    # 0x22. Named to match the services registry and the LID role canair reports elsewhere.
    return f"ReadDataByLocalIdentifier: LID 0x{data[1]:02X}, {len(data) - 2} data bytes"


def _write_did(data: bytes) -> str:
    return f"WriteDataByIdentifier: DID 0x{_word(data, 1):04X} accepted"


def _io_control(data: bytes) -> str:
    ctrl_name = _sub(INPUT_OUTPUT_CONTROL_BY_IDENTIFIER, data[3]) if len(data) >= 4 else "?"
    status = data[4:].hex().upper() if len(data) > 4 else ""
    result = f"IOControl: DID 0x{_word(data, 1):04X}, {ctrl_name}"
    return result + (f", status={status}" if status else "")


def _routine_control(data: bytes) -> str:
    return f"RoutineControl: {_sub(ROUTINE_CONTROL, data[1])} routine 0x{_word(data, 2):04X}"


def _dtc_information(data: bytes) -> str:
    name = subfunction_name(READ_DTC_INFORMATION, data[1]) or f"subFunction 0x{data[1]:02X}"
    return f"ReadDTCInformation: {name}, {len(data) - 2} data bytes"


def _read_memory(data: bytes) -> str:
    return f"ReadMemoryByAddress: {len(data) - 1} bytes returned"


def _transfer_block_size(data: bytes) -> str:
    """The block size a RequestDownload/RequestUpload reply allows.

    The reply carries a *lengthFormatIdentifier*, not the request's
    addressAndLengthFormatIdentifier: ISO 14229-1 defines its high nibble as the byte-length
    of the maxNumberOfBlockLength that follows, and its low nibble as RESERVED (0). Reporting
    that low nibble as an "addrLen" reads request semantics into a response; the useful datum
    is the block size itself.
    """
    n = (data[1] >> 4) & 0xF
    block = data[2 : 2 + n]
    if n and len(block) == n:
        return f"max block {int.from_bytes(block, 'big')} bytes"
    return f"max-block length field {n} byte(s), value truncated"


def _download(data: bytes) -> str:
    return (
        "WARNING: RequestDownload ACCEPTED — ECU ready to receive firmware "
        f"({_transfer_block_size(data)})"
    )


def _upload(data: bytes) -> str:
    return (
        f"WARNING: RequestUpload ACCEPTED — ECU ready to send memory ({_transfer_block_size(data)})"
    )


def _transfer_data(data: bytes) -> str:
    return f"WARNING: TransferData — block {data[1]}, {len(data) - 2} bytes being transferred"


def _transfer_exit(data: bytes) -> str:
    return "WARNING: TransferExit — firmware transfer completed"


def _communication_control(data: bytes) -> str:
    # The reply carries a controlType, not an identifier.
    return f"CommunicationControl: {_sub(COMMUNICATION_CONTROL, data[1])}"


def _dynamically_define(data: bytes) -> str:
    # The define variants echo the 2-byte dynamic DID they defined; a clear may omit it.
    sub = data[1]
    name = subfunction_name(DYNAMICALLY_DEFINE_DATA_IDENTIFIER, sub) or f"subFunction 0x{sub:02X}"
    result = f"DynamicallyDefineDataIdentifier: {name}"
    if len(data) >= 4:
        result += f", dynamic DID 0x{_word(data, 2):04X}"
    return result


def _control_dtc_setting(data: bytes) -> str:
    # The reply carries a DTCSettingType, not a DID.
    return (
        f"WARNING: ControlDTCSetting — DTC setting {_sub(CONTROL_DTC_SETTING, data[1])}, "
        "DTC logging may be altered"
    )


def _tester_present(data: bytes) -> str:
    return "TesterPresent: acknowledged"


# Request SID -> (shortest reply the describer can read, describer).
_BY_SERVICE: dict[int, tuple[int, Callable[[bytes], str]]] = {
    DIAGNOSTIC_SESSION_CONTROL: (2, _session),
    ECU_RESET: (2, _reset),
    SECURITY_ACCESS: (2, _security_access),
    READ_DATA_BY_IDENTIFIER: (3, _read_did),
    OBD_SHOW_CURRENT_DATA: (2, _obd_current_data),
    OBD_REQUEST_VEHICLE_INFORMATION: (2, _obd_vehicle_information),
    KWP_READ_DATA_BY_LOCAL_IDENTIFIER: (2, _read_local_id),
    WRITE_DATA_BY_IDENTIFIER: (3, _write_did),
    INPUT_OUTPUT_CONTROL_BY_IDENTIFIER: (3, _io_control),
    ROUTINE_CONTROL: (4, _routine_control),
    READ_DTC_INFORMATION: (2, _dtc_information),
    READ_MEMORY_BY_ADDRESS: (2, _read_memory),
    REQUEST_DOWNLOAD: (2, _download),
    REQUEST_UPLOAD: (2, _upload),
    TRANSFER_DATA: (2, _transfer_data),
    REQUEST_TRANSFER_EXIT: (1, _transfer_exit),
    COMMUNICATION_CONTROL: (2, _communication_control),
    DYNAMICALLY_DEFINE_DATA_IDENTIFIER: (2, _dynamically_define),
    CONTROL_DTC_SETTING: (2, _control_dtc_setting),
    TESTER_PRESENT: (1, _tester_present),
}
_DESCRIBERS = {positive_response_sid(sid): entry for sid, entry in _BY_SERVICE.items()}


def decode_uds_response(data: bytes) -> str | None:
    """Return a human-readable one-line decode of a UDS response, or None."""
    entry = _DESCRIBERS.get(data[0]) if data else None
    if entry is None or len(data) < entry[0]:
        return None
    return entry[1](data)
