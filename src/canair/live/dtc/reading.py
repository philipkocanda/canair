"""Read one ECU's stored DTCs: UDS ``19 02`` or KWP2000 ``18``, with their fallbacks."""

from __future__ import annotations

import sys

from canair.dtc.describe import describe_dtc
from canair.profile.model import Profile
from canair.uds.echo import request_echo
from canair.uds.nrc import NRC_REQUEST_OUT_OF_RANGE
from canair.uds.protocol import KWP, UDS
from canair.uds.services import KWP_READ_DTC_BY_STATUS, READ_DTC_INFORMATION
from canair.uds.subfunctions import J2012_DTC_FORMATS, DtcFormatIdentifier

from ...ecus.names import ecu_display, ecu_name
from ...transport.protocol import Terminal
from .codes import decode_dtc_records, decode_kwp_dtc_records, resolve_protocol

# UDS status mask to fall back to when an ECU rejects 0xFF with requestOutOfRange
# (NRC 0x31). Some ECUs only accept a mask within their availability bits;
# confirmedDTC (0x08, ISO 14229-1 statusOfDTC bit 3) is the most widely
# supported, so it is the make-neutral fallback.
_MASK_FALLBACK = 0x08

# KWP2000 readDiagnosticTroubleCodesByStatus (0x18) operand forms vary between
# ECUs/marques; try the common ISO 14230 forms until one returns a positive 0x58
# response. Reads are non-mutative, so probing is safe.
_KWP_READ_REQUESTS = ("1800FF00", "1802FF00", "1800FFFF")

# KWP2000 "request supported 2-byte hex DTCs and status" (DaimlerChrysler/Mitsubishi), "request
# all DTCs and status" in SSF 14230-3. It lists every DTC the ECU can report whatever its status, so
# it is never a fallback for the forms above (a healthy ECU would look faulty) and is only sent on
# request (``--supported``). The DTCs come back as raw 2-byte values, not SAE J2012 ones.
_KWP_SUPPORTED_REQUEST = "1803FF00"
_KWP_SUPPORTED_NOTE = (
    "lists every DTC this ECU supports, whatever its status; none of them is necessarily "
    "stored or active. Codes are shown as raw 2-byte hex because this request's DTC format is "
    "not SAE J2012. The status byte is shown raw."
)

# What a reader should know when only an alternate form is answered. The first form is the one
# Hyundai/Kia ECUs are known to accept (the DaimlerChrysler/Mitsubishi KWP2000 spec's "identified
# DTCs, SAE J2012 format, all groups"), so it needs no note. The others differ in meaning between
# KWP2000 documents, and canair formats every code as SAE J2012 regardless.
_KWP_FORM_NOTES = {
    "1802FF00": (
        "answered the alternate form 18 02 FF 00, not 18 00 FF 00. Status 02 selects 2-byte hex "
        "DTCs (DaimlerChrysler/Mitsubishi) or stored DTCs (SSF 14230-3); the codes below are "
        "formatted as SAE J2012 and may not be real P/C/B/U codes."
    ),
    "1800FFFF": (
        "answered the alternate form 18 00 FF FF, not 18 00 FF 00. Group FFFF is SSF 14230-3's "
        "'all DTCs'; the DTC format is the same as the primary form."
    ),
}


async def _read_one(
    terminal: Terminal,
    tx_id: int,
    mask: int,
    protocol: str,
    verbose: bool,
    timeout: float = 3.0,
    supported: bool = False,
    *,
    profile: Profile,
) -> dict:
    """Set the header and read DTCs from one ECU (no session handling)."""
    proto = resolve_protocol(protocol, tx_id, profile=profile)
    await terminal.set_header(tx_id)
    if proto == KWP:
        return await _read_kwp(
            terminal, tx_id, timeout=timeout, supported=supported, profile=profile
        )
    return await _read_uds(terminal, tx_id, mask, verbose, timeout=timeout, profile=profile)


async def _send_report(terminal: Terminal, cmd: str, timeout: float):
    """Send a ReadDTCInformation request; the reply must repeat its report type (``59 02``)."""
    expected = request_echo(cmd, UDS)
    return await terminal.send_uds(
        cmd,
        timeout=timeout,
        expected_sid=READ_DTC_INFORMATION,
        expected_echo=expected[1] if expected is not None else None,
    )


async def _read_dtc_format(terminal: Terminal, mask_hex: str, timeout: float) -> int | None:
    """The ECU's DTCFormatIdentifier, from ``19 01 <mask>`` (``59 01 avail format count``).

    None when the ECU does not answer it: not every ECU implements reportNumberOfDTC
    (the Ioniq's amplifier refuses it with 0x12), and its codes are then read as SAE J2012.
    """
    response = await _send_report(terminal, f"1901{mask_hex}", timeout)
    data = response.get("bytes") or b""
    return data[3] if response["ok"] and len(data) >= 6 else None


async def _read_uds(
    terminal: Terminal,
    tx_id: int,
    mask: int,
    verbose: bool,
    timeout: float = 3.0,
    *,
    profile: Profile,
) -> dict:
    ecu = ecu_display(tx_id, profile=profile)
    cmd = f"1902{mask & 0xFF:02X}"
    response = await _send_report(terminal, cmd, timeout)

    # Mask fallback: some ECUs reject 0xFF with requestOutOfRange (0x31) and only
    # accept a mask within their availability bits. Retry once with confirmedDTC.
    if (
        not response["ok"]
        and response.get("nrc") == NRC_REQUEST_OUT_OF_RANGE
        and (mask & 0xFF) != _MASK_FALLBACK
    ):
        if verbose:
            print(
                f"  mask 0x{mask & 0xFF:02X} rejected (0x31); retrying with 0x{_MASK_FALLBACK:02X}",
                file=sys.stderr,
                flush=True,
            )
        cmd = f"1902{_MASK_FALLBACK:02X}"
        response = await _send_report(terminal, cmd, timeout)

    base = {"ecu": ecu, "tx": f"0x{tx_id:03X}", "protocol": UDS, "command": cmd}
    if response["ok"]:
        data = response["bytes"]
        avail = data[2] if len(data) >= 3 else None
        records = decode_dtc_records(bytes(data[3:]))
        dtc_format = None
        if records:
            # Only an ECU that reported codes is asked how to read them: one more request.
            dtc_format = await _read_dtc_format(terminal, cmd[4:6], timeout)
            if dtc_format is not None:
                records = decode_dtc_records(bytes(data[3:]), dtc_format)
        return {
            **base,
            "status_availability_mask": f"0x{avail:02X}" if avail is not None else None,
            "dtc_format": f"0x{dtc_format:02X}" if dtc_format is not None else None,
            "count": len(records),
            "dtcs": _jsonable(records, tx_id, dtc_format, profile=profile),
        }
    if response.get("nrc") is not None:
        return {**base, "nrc": f"0x{response['nrc']:02X}", "nrc_desc": response["nrc_desc"]}
    return {**base, "error": response.get("error", "unknown")}


async def _read_kwp(
    terminal: Terminal,
    tx_id: int,
    timeout: float = 3.0,
    supported: bool = False,
    *,
    profile: Profile,
) -> dict:
    ecu = ecu_display(tx_id, profile=profile)
    response = None
    requests = (_KWP_SUPPORTED_REQUEST,) if supported else _KWP_READ_REQUESTS
    cmd = requests[0]
    for cmd in requests:
        response = await terminal.send_uds(
            cmd, timeout=timeout, expected_sid=KWP_READ_DTC_BY_STATUS
        )
        if response["ok"]:
            break
        # Only probe alternate request forms if the ECU actually answered (NRC);
        # no response means the ECU isn't present — stop probing.
        if response.get("nrc") is None:
            break

    # _KWP_READ_REQUESTS is non-empty, so the loop always ran and set `response`.
    assert response is not None
    base = {"ecu": ecu, "tx": f"0x{tx_id:03X}", "protocol": KWP, "command": cmd}
    if response["ok"]:
        data = response["bytes"]
        # 58 <count> [dtc_hi dtc_lo status]* — count is advisory; decode by length.
        records = decode_kwp_dtc_records(bytes(data[2:]))
        if supported:
            return {
                **base,
                "supported": True,
                "count": len(records),
                "dtcs": [
                    {
                        "dtc": f"0x{r['raw']}",
                        "raw": r["raw"],
                        "status": f"0x{r['status']:02X}",
                        "status_bits": [],
                    }
                    for r in records
                ],
                "note": _KWP_SUPPORTED_NOTE,
            }
        result = {**base, "count": len(records), "dtcs": _jsonable(records, tx_id, profile=profile)}
        if cmd in _KWP_FORM_NOTES:
            result["note"] = _KWP_FORM_NOTES[cmd]
        return result
    if response.get("nrc") is not None:
        return {**base, "nrc": f"0x{response['nrc']:02X}", "nrc_desc": response["nrc_desc"]}
    return {**base, "error": response.get("error", "unknown")}


def _jsonable(
    records: list[dict], tx_id: int, dtc_format: int | None = None, *, profile: Profile
) -> list[dict]:
    """Render internal records (int status) into the JSON/print-friendly shape,
    annotated with each code's interpretation on the ECU ``tx_id`` that reported it.

    A code in a format other than SAE J2012 has no standard reading, so it gets none.
    """
    ecu = ecu_name(tx_id, profile=profile)
    j2012 = dtc_format is None or dtc_format in J2012_DTC_FORMATS
    return [
        {
            "dtc": r["dtc"],
            "raw": r["raw"],
            "status": f"0x{r['status']:02X}",
            "status_bits": r["status_bits"],
            "interpretation": describe_dtc(r["dtc"], ecu=ecu, profile=profile) if j2012 else None,
        }
        for r in records
    ]


def dtc_format_note(dtc_format: str | None) -> str | None:
    """What to say about a reported DTCFormatIdentifier (``"0x01"``), or None for J2012."""
    if dtc_format is None:
        return None
    value = int(dtc_format, 16)
    if value in J2012_DTC_FORMATS:
        return None
    try:
        name = DtcFormatIdentifier(value).name
    except ValueError:
        name = f"reserved format {dtc_format}"
    return (
        f"the ECU reports its codes in {name} (DTCFormatIdentifier {dtc_format}), which has no "
        "standard text form: they are shown as hex, and not looked up as SAE J2012 codes"
    )
