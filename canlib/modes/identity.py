"""Query ECU identity data across UDS and KWP2000 diagnostic protocols.

``mode_identity`` picks the right identity service from an explicit
``--protocol`` flag, then the profile's ``id_protocol`` registry hint, then a
lightweight on-device probe — so it works across a broad range of ECUs and
manufacturers instead of silently printing nothing on KWP2000 ECUs.

The record tables live in :mod:`identity_records` and the pure decode /
protocol-selection helpers in :mod:`identity_decode`; this module is just the
async device orchestration.
"""

import asyncio
import json

from canlib.uds.echo import hk_identity_offset, request_echo
from canlib.uds.protocol import KWP, UDS
from canlib.uds.response import UdsResponse
from canlib.uds.services import RESPONSE_SID_OFFSET

from ..ecus import ecu_display
from ..quirks import HK_F1XX_MINUS_ONE
from ..sensitive import MASK, request_hidden
from ..transport.protocol import Terminal
from .identity_decode import (
    decode_identity_payload,
    resolve_protocol_hint,
    service_supported,
)
from .identity_records import (
    IDENTITY_DIDS,
    KWP_IDENTITY_RECORDS,
    PROTOCOLS,
    UDS_IDENTITY_DIDS,
    records_for,
)

__all__ = [
    "IDENTITY_DIDS",
    "KWP_IDENTITY_RECORDS",
    "UDS_IDENTITY_DIDS",
    "mode_identity",
]


async def _read(terminal: Terminal, request: str) -> UdsResponse:
    """Send one identity read, validated against the echo its reply must carry.

    Without it a late reply to the previous record would be printed under this
    record's label (the terminal's HK F1xx quirk still applies).
    """
    echo = request_echo(request)
    if echo is None:
        return await terminal.send_uds(request)
    return await terminal.send_uds(request, expected_sid=echo[0], expected_echo=echo[1])


def _offset_answer(request: str, response: UdsResponse) -> str | None:
    """The identifier a refused F1xx read was answered with, when it is exactly one lower.

    That is how Hyundai/Kia ECUs answer (``22F188`` -> ``62F187``), which a profile
    accepts only with the :data:`~canlib.quirks.HK_F1XX_MINUS_ONE` quirk. Without it
    the reply stays refused — on another make it is a stale reply to the previous
    record — but it is reported as what it most likely is rather than as silence.
    """
    echo = request_echo(request)
    data = response.get("bytes")
    if response.get("ok") or echo is None or data is None or len(data) < 3:
        return None
    sid, expected = echo
    if data[0] != sid + RESPONSE_SID_OFFSET or not hk_identity_offset(sid, expected, data[1:3]):
        return None
    return data[1:3].hex().upper()


def _offset_hint(offsets: list[tuple[str, str]]) -> list[str]:
    pairs = ", ".join(f"{asked}->{got}" for asked, got in offsets)
    return [
        f"\n  {len(offsets)} field(s) answered with the identifier one lower ({pairs}):",
        "  the Hyundai/Kia identity offset. On a Hyundai/Kia ECU, add",
        f"  `{HK_F1XX_MINUS_ONE}` to `quirks:` in the profile's profile.yaml to read them.",
    ]


async def _probe_protocol(terminal: Terminal) -> tuple[str | None, str]:
    """Probe the ECU to decide UDS vs KWP2000. Returns (protocol, reason)."""
    uds = await _read(terminal, "22F190")
    # A 62 reply proves service 22 even when it names the identifier one lower.
    uds_ok = True if _offset_answer("22F190", uds) else service_supported(uds)
    if uds_ok:
        return UDS, "UDS service 22 supported (probe 22F190)"

    kwp = await _read(terminal, "1A90")
    kwp_ok = service_supported(kwp)
    if kwp_ok:
        return KWP, "KWP2000 service 1A supported (probe 1A90)"

    if uds_ok is False and kwp_ok is False:
        return None, (
            "neither UDS 22 nor KWP2000 1A supported (NRC 0x11, or 0x7F/0x80 "
            "refused in the active session)"
        )
    return None, "no response to probe (22F190 / 1A90) — ECU may be asleep or unpowered"


async def mode_identity(
    terminal: Terminal,
    tx_id: int,
    session: bool,
    wake: bool,
    as_json: bool,
    protocol: str = "auto",
    quirks: frozenset[str] | None = None,
):
    """Query identity DIDs/records from an ECU across UDS and KWP2000.

    ``quirks`` is the active profile's declared quirk set; when None it is
    resolved from the active profile. It gates make-specific identity DIDs
    (e.g. the HK-only ``F187``) so a make-neutral profile never probes them.
    """
    if quirks is None:
        from ..profile import active
        from ..quirks import resolve_quirks

        quirks = resolve_quirks(active().meta)

    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    try:
        resolved = resolve_protocol_hint(tx_id, protocol)
        probe_reason = None
        if resolved is None:
            resolved, probe_reason = await _probe_protocol(terminal)

        if not as_json:
            print(f"\n  Identity query: {ecu_display(tx_id)}")
            if resolved:
                fam = "UDS (22 F1xx)" if resolved == UDS else "KWP2000 (1A 8x/9x)"
                detail = f" — {probe_reason}" if probe_reason else ""
                print(f"  Protocol: {fam}{detail}\n")
            else:
                print()

        if resolved is None:
            msg = probe_reason or "could not determine identity protocol"
            if as_json:
                print(json.dumps({"ecu": ecu_display(tx_id), "protocol": None, "results": []}))
            else:
                print(f"  No identity data: {msg}.")
                if "asleep" in msg:
                    print("  Try --session/--wake, or power the ECU (ACC/ignition on).")
            return

        spec = PROTOCOLS[resolved]
        records = records_for(resolved, quirks)
        offset = spec["payload_offset"]
        prefix = spec["prefix"]
        label_width = max(len(label) for _, label, _ in records)

        results = []
        n_ok = n_nrc = n_nodata = 0
        offsets: list[tuple[str, str]] = []
        responded: dict[str, bool] = {}

        for did_hex, label, fmt in records:
            request = f"{prefix}{did_hex}"
            response = await _read(terminal, request)
            responded[request] = bool(response["ok"])
            if response["ok"]:
                n_ok += 1
                payload = response["bytes"][offset:]
                decoded = decode_identity_payload(payload, fmt)
                raw_hex = payload.hex().upper()
                if request_hidden(tx_id, f"{prefix}{did_hex}"):
                    decoded = raw_hex = MASK
                if as_json:
                    results.append(
                        {
                            "service": prefix,
                            "did": did_hex,
                            "label": label,
                            "decoded": decoded,
                            "raw": raw_hex,
                        }
                    )
                else:
                    print(f"  {did_hex}  {label:<{label_width}}  {decoded}")
                    if (fmt != "ascii" or "." in decoded) and decoded != raw_hex:
                        print(f"        {'':>{label_width}}  raw: {raw_hex}")
            elif response.get("nrc") is not None:
                n_nrc += 1
            elif (answered_as := _offset_answer(request, response)) is not None:
                offsets.append((did_hex, answered_as))
                if not as_json:
                    print(f"  {did_hex}  {label:<{label_width}}  (answered as {answered_as})")
            else:
                n_nodata += 1

        from .revision_probe import describe_identity_report, identity_report

        try:
            revision = identity_report(tx_id, responded) if n_ok else None
        except Exception:  # an advisory report never fails the reading itself
            revision = None
        if as_json:
            out = {
                "ecu": ecu_display(tx_id),
                "protocol": resolved,
                "summary": {
                    "ok": n_ok,
                    "nrc": n_nrc,
                    "no_data": n_nodata,
                    "offset": len(offsets),
                },
                "results": results,
            }
            if offsets:
                out["offset_answers"] = [
                    {"did": asked, "answered_as": got, "quirk": HK_F1XX_MINUS_ONE}
                    for asked, got in offsets
                ]
            if revision is not None:
                out["revision"] = revision
            print(json.dumps(out, indent=2))
        else:
            if n_ok == 0 and not offsets:
                if n_nodata and not n_nrc:
                    print("  No response — ECU may be asleep or unpowered.")
                    print("  Try --session/--wake, or power the ECU (ACC/ignition on).")
                else:
                    print("  No identity fields returned (all DIDs rejected/unsupported).")
            offset_note = f", {len(offsets)} answered one lower" if offsets else ""
            print(
                f"\n  {n_ok} field(s) returned, {n_nrc} rejected, "
                f"{n_nodata} no-response{offset_note} (of {len(records)} probed)."
            )
            if offsets:
                print("\n".join(_offset_hint(offsets)))
            if revision is not None:
                print("\n".join(describe_identity_report(revision)))

    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass
