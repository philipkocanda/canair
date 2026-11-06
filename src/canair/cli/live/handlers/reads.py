"""Handlers for the plain read surfaces: identity, named signals, raw hex.

Read-only single requests. Each resolves its ECU/PID arguments, reports a clean
error for an unresolvable one, and hands off to its mode.

``identity`` is the one that may write: when its reading proves the protocol of an ECU whose
profile names none, it offers to record ``identity.id_protocol``
(:func:`offer_protocol_record`). :func:`confirm_unknown_protocol` is the question the routine
paths ask about such an ECU, and it points here.
"""

from __future__ import annotations

import sys

from canair.cli.commands._edit import edit_scope
from canair.cli.profile_context import active
from canair.core.prompt import confirm
from canair.ecus.names import ecu_name, resolve_tx
from canair.ecus.pids import load_raw_pids
from canair.live.identity.read import ProtocolFinding, mode_identity
from canair.live.read.param import mode_param
from canair.live.read.raw import mode_raw
from canair.profile.model import Profile
from canair.render import messages
from canair.safety.blocklist import SessionPolicy
from canair.transport.protocol import Terminal
from canair.uds.protocol import UNKNOWN, id_protocol_value
from canair.yaml_edit.echo import echo_edit
from canair.yaml_edit.pids import set_identity_field


def unknown_protocol_ecus(names: list[str], pids_data: dict, *, profile: Profile) -> list[str]:
    """The ``names`` whose definitions record no protocol (or that are not registered).

    Judged by the :class:`~canair.safety.blocklist.SessionPolicy` of ``pids_data``, the same
    reading the transports and the routine refusal use.
    """
    policy = SessionPolicy.from_profile(pids_data)
    tx_ids = {
        str(name).upper(): ecu_def.get("tx_id")
        for name, ecu_def in (pids_data.get("ecus") or {}).items()
        if isinstance(ecu_def, dict)
    }
    out = []
    for name in names:
        tx_id = tx_ids.get(name.upper()) or resolve_tx(name, profile=profile)
        if policy.protocol(tx_id) == UNKNOWN:
            out.append(name)
    return out


def confirm_unknown_protocol(
    names: list[str], what: str, pids_data: dict, *, yes: bool, profile: Profile
) -> bool:
    """Warn that ``what`` (a UDS request) starts a routine on a KWP2000 ECU, and ask.

    Asks only about the ``names`` whose protocol is unknown (:func:`unknown_protocol_ecus`).
    True when the user (or ``yes``) says go, or when there are none.
    """
    unknown = unknown_protocol_ecus(names, pids_data, profile=profile)
    if not unknown:
        return True
    listed = ", ".join(unknown)
    messages.warning(
        f"the profile records no protocol for {listed}, so canair cannot tell UDS from KWP2000\n"
        f"{what} is UDS RoutineControl, which a KWP2000 ECU reads as "
        "StartRoutineByLocalIdentifier and runs"
    )
    messages.hint(
        f"run `canair identity {unknown[0]}` first: it finds the protocol and offers to record it"
    )
    return confirm(f"Send {what} to {listed} anyway?", yes=yes, danger=True)


async def handle_identity(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    tx_id = resolve_tx(args.tx, profile=active())
    if tx_id is None:
        messages.fail(
            f"could not resolve ECU '{args.tx}' (use a name like IGPM or a hex TX id like 770)"
        )
    finding = await mode_identity(
        terminal,
        tx_id,
        session=args.session,
        wake=args.wake,
        as_json=args.json,
        protocol=getattr(args, "protocol", "auto"),
        profile=active(),
    )
    if finding is not None:
        offer_protocol_record(
            finding, yes=getattr(args, "set_protocol", False), as_json=args.json, profile=active()
        )


def offer_protocol_record(
    finding: ProtocolFinding, *, yes: bool, as_json: bool, profile: Profile
) -> None:
    """Offer to write the protocol a reading proved into the ECU's ``identity.id_protocol``.

    ``yes`` (``--set-protocol``) writes it without asking. With no terminal and no flag, a hint
    names the flag instead of a question, since declining changes nothing. An ECU the profile
    cannot take the field for (unregistered, revision-dependent, no ``identity:``, a read-only
    layered profile) gets a hint saying why.
    """
    value = id_protocol_value(finding.protocol)
    name = ecu_name(finding.tx_id, profile=profile)
    ecu_def = load_raw_pids(profile).get("ecus", {}).get(name)
    reason = _cannot_record(ecu_def, profile)
    if reason is not None:
        messages.hint(f"{name} speaks {value} ({finding.evidence}), but {reason}")
        return
    if not yes and not sys.stdin.isatty():
        messages.hint(
            f"{name} speaks {value} ({finding.evidence}); pass --set-protocol to record it"
        )
        return
    current = ecu_def["identity"].get("id_protocol")  # type: ignore[index]
    replaces = f", replacing {current}" if current else ""
    question = f"{name} speaks {value} ({finding.evidence}). Record id_protocol: {value}{replaces}?"
    if not confirm(question, yes=yes):
        return
    with edit_scope():
        path = set_identity_field(name, "id_protocol", value, profile=profile)
    echo_edit(
        f"{name} identity.id_protocol set to {value}", path, file=sys.stderr if as_json else None
    )


def _cannot_record(ecu_def: object, profile: Profile) -> str | None:
    if not isinstance(ecu_def, dict):
        return "it is not registered (`canair ecu add TX --id-protocol …` registers it)"
    if ecu_def.get("revisions"):
        return "its protocol depends on its revision (`canair ecu revisions`)"
    if not isinstance(ecu_def.get("identity"), dict):
        return "its profile entry has no identity: section"
    if profile.layered:
        return f"'{profile.name}' is layered and its definitions are read-only"
    return None


async def handle_param(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    await mode_param(
        terminal,
        pids_data,
        args.param,
        args.verbose,
        args.json,
        session=args.session,
        wake=args.wake,
    )


async def handle_raw(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    await mode_raw(
        terminal,
        args.raw,
        args.verbose,
        args.json,
        session=args.session,
        hold=args.hold,
        wake=args.wake,
        save=args.save,
        pids_data=pids_data,
        label=args.label,
        vehicle_states=args.state,
        notes=args.notes,
        profile=active(),
    )
