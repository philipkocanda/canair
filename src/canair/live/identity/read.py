"""Query ECU identity data across UDS and KWP2000 diagnostic protocols.

``mode_identity`` picks the right identity service from an explicit
``--protocol`` flag, then the profile's ``id_protocol`` registry hint, then a
lightweight on-device probe — so it works across a broad range of ECUs and
manufacturers instead of silently printing nothing on KWP2000 ECUs.

The record tables live in :mod:`.records` and the pure decode / protocol-selection
helpers in :mod:`.decode`; this module is just the async device orchestration.

When the profile names no protocol, both probes are sent, and a reading whose fields came
back on one protocol while the other's service was refused *proves* the protocol. The
operation returns that :class:`ProtocolFinding`, so the command can offer to record it in
the profile (``identity.id_protocol``), which the routine and IOControl paths read to tell
UDS from KWP2000.

An ECU that has not answered :data:`SILENT_READS_BEFORE_STOP` reads in a row is not asked
the rest of the table, and the NRCs the records draw pick the hint
(:func:`.decode.refusal_hint`).
"""

import json
import sys
from collections import Counter
from dataclasses import dataclass

from canair.privacy.sensitive import MASK, request_hidden
from canair.profile.model import Profile
from canair.render import ansi, messages
from canair.render.paint import Painter
from canair.uds.echo import request_echo
from canair.uds.protocol import KWP, UDS, DiagProtocol
from canair.uds.response import CAT_DECODE, CAT_DROP, CAT_STALE, UdsResponse

from ...ecus.names import ecu_display
from ...transport.protocol import Terminal
from ..hold import held_session
from ..revision_probe import describe_identity_report, identity_report
from .decode import (
    SESSION_NRCS,
    ProbeState,
    decide_protocol,
    decode_identity_payload,
    probe_state,
    refusal_hint,
    resolve_protocol_hint,
)
from .records import IDENTITY_DIDS, KWP_IDENTITY_RECORDS, PROTOCOLS, UDS_IDENTITY_DIDS

__all__ = [
    "IDENTITY_DIDS",
    "KWP_IDENTITY_RECORDS",
    "UDS_IDENTITY_DIDS",
    "ProtocolFinding",
    "mode_identity",
]

# Reads in a row that nothing answered, with no earlier answer at all, before the loop stops.
SILENT_READS_BEFORE_STOP = 3


@dataclass(frozen=True)
class ProbeResult:
    """What the two protocol probes said: the protocol they point to, why, and each reply with
    the NRC that drew it."""

    protocol: DiagProtocol | None
    reason: str
    uds: ProbeState
    kwp: ProbeState
    uds_nrc: int | None = None
    kwp_nrc: int | None = None


@dataclass(frozen=True)
class ProtocolFinding:
    """A protocol the reading proved for an ECU whose profile names none.

    ``evidence`` says why, for the question that offers to record it.
    """

    tx_id: int
    protocol: DiagProtocol
    evidence: str


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


async def _probe_protocol(terminal: Terminal) -> ProbeResult:
    """Probe the ECU with both identity services and decide UDS vs KWP2000."""
    uds_reply = await _read(terminal, "22F190")
    kwp_reply = await _read(terminal, "1A90")
    uds, kwp = probe_state(uds_reply), probe_state(kwp_reply)
    uds_nrc, kwp_nrc = uds_reply.get("nrc"), kwp_reply.get("nrc")
    protocol, reason = decide_protocol(uds, kwp, uds_nrc, kwp_nrc)
    return ProbeResult(protocol, reason, uds, kwp, uds_nrc, kwp_nrc)


def _probe_refused_in_session(probe: ProbeResult | None, protocol: str | None) -> bool:
    """True when the probe of ``protocol`` itself drew a "not in this session" refusal."""
    if probe is None:
        return False
    state = probe.uds if protocol == UDS else probe.kwp
    return state == "session"


def _is_silent(response: UdsResponse) -> bool:
    """True when nothing answered: no NRC, and not a stale, dropped or undecodable reply."""
    return (
        not response["ok"]
        and response.get("nrc") is None
        and response.get("error_kind") not in (CAT_STALE, CAT_DECODE, CAT_DROP)
    )


def _finding(tx_id: int, probe: ProbeResult | None, n_ok: int) -> ProtocolFinding | None:
    """The protocol this reading proves, or None.

    Proof is identity fields read on one protocol while the other protocol's service was
    refused with serviceNotSupported (``0x11``). Fields alone are not enough: a KWP2000 ECU
    may answer some 22 reads too. A refusal for the active session only (``0x7F``/``0x80``)
    is not enough either, because that service may exist in another session.
    """
    if probe is None or probe.protocol is None or n_ok == 0:
        return None
    if probe.protocol == UDS and probe.kwp == "refused":
        return ProtocolFinding(tx_id, UDS, f"{n_ok} UDS identity field(s) read, KWP2000 1A refused")
    if probe.protocol == KWP and probe.uds == "refused":
        return ProtocolFinding(
            tx_id, KWP, f"{n_ok} KWP2000 identity record(s) read, UDS 22 refused"
        )
    return None


async def mode_identity(
    terminal: Terminal,
    tx_id: int,
    session: bool,
    wake: bool,
    as_json: bool,
    protocol: str = "auto",
    *,
    profile: Profile,
) -> ProtocolFinding | None:
    """Query identity DIDs/records from an ECU across UDS and KWP2000.

    Returns the protocol the reading proved when the profile names none and the probe ran
    (:func:`_finding`), else None.
    """
    paint = Painter(sys.stdout)

    await terminal.set_header(tx_id)

    async with held_session(terminal, tx_id, profile=profile, enabled=session, wake=wake):
        resolved = resolve_protocol_hint(tx_id, protocol, profile=profile)
        probe_reason = None
        probe = None
        if resolved is None:
            probe = await _probe_protocol(terminal)
            resolved, probe_reason = probe.protocol, probe.reason

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
                            "probe": _probe_json(probe),
                            "results": [],
                        }
                    )
                )
            else:
                print(f"  No identity data: {msg}.")
            if probe is not None and probe.uds == probe.kwp == "silent":
                messages.hint("try --session/--wake, or power the ECU (ACC/ignition on)")
            if probe is not None and not session and {probe.uds_nrc, probe.kwp_nrc} & SESSION_NRCS:
                messages.hint("a probe was refused in this session: try --session")
            return None

        spec = PROTOCOLS[resolved]
        records = spec["records"]
        offset = spec["payload_offset"]
        prefix = spec["prefix"]
        label_width = max(len(label) for _, label, _ in records)
        did_width = max(len(did) for did, _, _ in records)

        results = []
        n_ok = n_nrc = n_nodata = 0
        nrc_codes: Counter[int] = Counter()
        silent_run = 0
        responded: dict[str, bool] = {}
        # The probe's own DID drew the session refusal, so the records draw it too.
        skip_reads = not session and _probe_refused_in_session(probe, resolved)

        for did_hex, label, fmt in records:
            if skip_reads or (
                probe is None and not (n_ok or n_nrc) and silent_run >= SILENT_READS_BEFORE_STOP
            ):
                break
            request = f"{prefix}{did_hex}"
            response = await _read(terminal, request)
            responded[request] = bool(response["ok"])
            silent_run = silent_run + 1 if _is_silent(response) else 0
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
                nrc_codes[response["nrc"]] += 1
            else:
                n_nodata += 1

        n_not_asked = len(records) - n_ok - n_nrc - n_nodata
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
                    "not_asked": n_not_asked,
                    "nrc_codes": {f"0x{code:02X}": n for code, n in sorted(nrc_codes.items())},
                },
                "results": results,
            }
            if revision is not None:
                out["revision"] = revision
            if probe is not None:
                out["probe"] = _probe_json(probe)
            print(json.dumps(out, indent=2))
        else:
            if n_ok == 0:
                if skip_reads:
                    print("  Records not read: the probe was refused in this session.")
                elif n_nodata and not n_nrc:
                    print("  No response — ECU may be asleep or unpowered.")
                else:
                    print("  No identity fields returned (all DIDs rejected/unsupported).")
            counts = f"{n_ok} field(s) returned, {n_nrc} rejected, {n_nodata} no-response"
            if n_not_asked:
                summary = f"{counts}, {n_not_asked} not asked (of {len(records)})."
            else:
                summary = f"{counts} (of {len(records)} probed)."
            print(f"\n  {paint(summary, ansi.raw.DIM)}")
            if revision is not None:
                print("\n".join(describe_identity_report(revision)))
        _hint_for_reading(
            nrc_codes, probe, resolved, n_ok, n_nodata, session=session, skip_reads=skip_reads
        )
    return _finding(tx_id, probe, n_ok)


def _hint_for_reading(
    nrc_codes: Counter[int],
    probe: ProbeResult | None,
    protocol: str | None,
    n_ok: int,
    n_nodata: int,
    *,
    session: bool,
    skip_reads: bool,
) -> None:
    """Print the one hint (stderr) that fits what the reading drew."""
    codes = nrc_codes
    if skip_reads and probe is not None:
        nrc = probe.uds_nrc if protocol == UDS else probe.kwp_nrc
        codes = Counter({nrc: 1}) if nrc is not None else codes
    if n_ok == 0 and n_nodata and not codes:
        messages.hint("try --session/--wake, or power the ECU (ACC/ignition on)")
        return
    text = refusal_hint(codes, n_ok, session=session, protocol=protocol)
    if text:
        messages.hint(text)


def _probe_json(probe: ProbeResult | None) -> dict | None:
    """The probe replies for ``--json``: each state is ``positive``, ``supported``, ``session``,
    ``refused`` or ``silent`` (:func:`.decode.probe_state`), with the NRC code that drew it."""
    if probe is None:
        return None
    return {
        "uds": probe.uds,
        "uds_nrc": None if probe.uds_nrc is None else f"0x{probe.uds_nrc:02X}",
        "kwp": probe.kwp,
        "kwp_nrc": None if probe.kwp_nrc is None else f"0x{probe.kwp_nrc:02X}",
    }
