"""Query ECU identity data across UDS and KWP2000 diagnostic protocols.

``mode_identity`` picks the right identity service from an explicit
``--protocol`` flag, then the profile's ``id_protocol`` registry hint, then a
lightweight on-device probe — so it works across a broad range of ECUs and
manufacturers instead of silently printing nothing on KWP2000 ECUs.

The record tables live in :mod:`identity_records` and the pure decode /
protocol-selection helpers in :mod:`identity_decode`; this module is just the
async device orchestration.
"""

import json
import sys

from canair.privacy.sensitive import MASK, request_hidden
from canair.profile.model import Profile
from canair.render import ansi
from canair.render.paint import Painter
from canair.uds.echo import request_echo
from canair.uds.protocol import KWP, UDS
from canair.uds.response import UdsResponse

from ...ecus.names import ecu_display
from ...transport.protocol import Terminal
from ..hold import held_session
from ..revision_probe import describe_identity_report, identity_report
from .decode import decode_identity_payload, resolve_protocol_hint, service_supported
from .records import IDENTITY_DIDS, KWP_IDENTITY_RECORDS, PROTOCOLS, UDS_IDENTITY_DIDS

__all__ = [
    "IDENTITY_DIDS",
    "KWP_IDENTITY_RECORDS",
    "UDS_IDENTITY_DIDS",
    "mode_identity",
]


async def _read(terminal: Terminal, request: str) -> UdsResponse:
    """Send one identity read, validated against the echo its reply must carry.

    Without it a late reply to the previous record would be printed under this
    record's label. Some ECUs answer a DID they do not support by repeating their
    previous reply; the check turns that into a stale reply, not a field.
    """
    echo = request_echo(request)
    if echo is None:
        return await terminal.send_uds(request)
    return await terminal.send_uds(request, expected_sid=echo[0], expected_echo=echo[1])


async def _probe_protocol(terminal: Terminal) -> tuple[str | None, str]:
    """Probe the ECU to decide UDS vs KWP2000. Returns (protocol, reason)."""
    uds = await _read(terminal, "22F190")
    uds_ok = service_supported(uds)
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
    *,
    profile: Profile,
):
    """Query identity DIDs/records from an ECU across UDS and KWP2000."""
    paint = Painter(sys.stdout)

    await terminal.set_header(tx_id)

    async with held_session(terminal, tx_id, profile=profile, enabled=session, wake=wake):
        resolved = resolve_protocol_hint(tx_id, protocol, profile=profile)
        probe_reason = None
        if resolved is None:
            resolved, probe_reason = await _probe_protocol(terminal)

        if not as_json:
            fam = ""
            if resolved:
                fam = "UDS (22 F1xx)" if resolved == UDS else "KWP2000 (1A 8x/9x)"
            print(
                f"\n  {paint(ecu_display(tx_id, profile=profile), ansi.raw.BOLD + ansi.raw.CYAN)}",
                end="",
            )
            print(paint(f" · identity · {fam}" if fam else " · identity", ansi.raw.DIM))
            if resolved and probe_reason:
                print(paint(f"  {probe_reason}", ansi.raw.DIM))
            print()

        if resolved is None:
            msg = probe_reason or "could not determine identity protocol"
            if as_json:
                print(
                    json.dumps(
                        {
                            "ecu": ecu_display(tx_id, profile=profile),
                            "protocol": None,
                            "results": [],
                        }
                    )
                )
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
        did_width = max(len(did) for did, _, _ in records)

        results = []
        n_ok = n_nrc = n_nodata = 0
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
                if request_hidden(tx_id, f"{prefix}{did_hex}", profile=profile):
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
                    did = paint(did_hex.ljust(did_width), ansi.raw.CYAN)
                    print(f"  {did}  {label:<{label_width}}  {decoded}")
                    if (fmt != "ascii" or "." in decoded) and decoded != raw_hex:
                        raw_line = f"{'':{did_width}}  {'':>{label_width}}  raw: {raw_hex}"
                        print(f"  {paint(raw_line, ansi.raw.DIM)}")
            elif response.get("nrc") is not None:
                n_nrc += 1
            else:
                n_nodata += 1

        try:
            revision = identity_report(tx_id, responded, profile=profile) if n_ok else None
        except Exception:  # an advisory report never fails the reading itself
            revision = None
        if as_json:
            out = {
                "ecu": ecu_display(tx_id, profile=profile),
                "protocol": resolved,
                "summary": {
                    "ok": n_ok,
                    "nrc": n_nrc,
                    "no_data": n_nodata,
                },
                "results": results,
            }
            if revision is not None:
                out["revision"] = revision
            print(json.dumps(out, indent=2))
        else:
            if n_ok == 0:
                if n_nodata and not n_nrc:
                    print("  No response — ECU may be asleep or unpowered.")
                    print("  Try --session/--wake, or power the ECU (ACC/ignition on).")
                else:
                    print("  No identity fields returned (all DIDs rejected/unsupported).")
            summary = (
                f"{n_ok} field(s) returned, {n_nrc} rejected, "
                f"{n_nodata} no-response (of {len(records)} probed)."
            )
            print(f"\n  {paint(summary, ansi.raw.DIM)}")
            if revision is not None:
                print("\n".join(describe_identity_report(revision)))
