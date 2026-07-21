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

from ..ecus import ecu_display
from ..terminal import WiCANTerminal
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
)

__all__ = [
    "IDENTITY_DIDS",
    "KWP_IDENTITY_RECORDS",
    "UDS_IDENTITY_DIDS",
    "mode_identity",
]


async def _probe_protocol(terminal: WiCANTerminal) -> tuple[str | None, str]:
    """Probe the ECU to decide UDS vs KWP2000. Returns (protocol, reason)."""
    uds = await terminal.send_uds("22F190")
    uds_ok = service_supported(uds)
    if uds_ok:
        return "uds", "UDS service 22 supported (probe 22F190)"

    kwp = await terminal.send_uds("1A90")
    kwp_ok = service_supported(kwp)
    if kwp_ok:
        return "kwp", "KWP2000 service 1A supported (probe 1A90)"

    if uds_ok is False and kwp_ok is False:
        return None, "neither UDS 22 nor KWP2000 1A supported (both NRC 0x11)"
    return None, "no response to probe (22F190 / 1A90) — ECU may be asleep or unpowered"


async def mode_identity(
    terminal: WiCANTerminal,
    tx_id: int,
    session: bool,
    wake: bool,
    as_json: bool,
    protocol: str = "auto",
):
    """Query identity DIDs/records from an ECU across UDS and KWP2000."""
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
                fam = "UDS (22 F1xx)" if resolved == "uds" else "KWP2000 (1A 8x/9x)"
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
        records = spec["records"]
        offset = spec["payload_offset"]
        prefix = spec["prefix"]
        label_width = max(len(label) for _, label, _ in records)

        results = []
        n_ok = n_nrc = n_nodata = 0

        for did_hex, label, fmt in records:
            response = await terminal.send_uds(f"{prefix}{did_hex}")
            if response["ok"]:
                n_ok += 1
                payload = response["bytes"][offset:]
                decoded = decode_identity_payload(payload, fmt)
                raw_hex = payload.hex().upper()
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
                    if fmt != "ascii" or "." in decoded:
                        print(f"        {'':>{label_width}}  raw: {raw_hex}")
            elif response.get("nrc") is not None:
                n_nrc += 1
            else:
                n_nodata += 1

        if as_json:
            print(
                json.dumps(
                    {
                        "ecu": ecu_display(tx_id),
                        "protocol": resolved,
                        "summary": {"ok": n_ok, "nrc": n_nrc, "no_data": n_nodata},
                        "results": results,
                    },
                    indent=2,
                )
            )
        else:
            if n_ok == 0:
                if n_nodata and not n_nrc:
                    print("  No response — ECU may be asleep or unpowered.")
                    print("  Try --session/--wake, or power the ECU (ACC/ignition on).")
                else:
                    print("  No identity fields returned (all DIDs rejected/unsupported).")
            print(
                f"\n  {n_ok} field(s) returned, {n_nrc} rejected, "
                f"{n_nodata} no-response (of {len(records)} probed)."
            )

    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass
