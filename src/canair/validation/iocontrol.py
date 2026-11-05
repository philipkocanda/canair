"""Validation of an ECU's ``iocontrol:`` block: entries, ``session_mode`` and ``release``."""

import re

from canair.uds.services import INPUT_OUTPUT_CONTROL_BY_IDENTIFIER
from canair.uds.subfunctions import IoControlParameter

from ._common import validate_state_list


def _validate_session_mode(
    value, label: str, errors: list, warnings: list, protocol="unknown", profile_meta=None
) -> None:
    """Validate an actuator's optional ``session_mode`` (one quoted hex byte).

    The byte is judged by the same rule the runtime guard applies
    (:func:`canair.safety.blocklist.session_mode_block`) for the ECU's protocol: a hard
    block (programming / safety-system session) is an error; a mode the guard
    would refuse without a profile allowance is a warning, because the actuator
    could only ever be reached with ``--unsafe``.
    """
    from canair.safety.blocklist import session_mode_block
    from canair.safety.quirks import allowed_session_modes

    if value is None:
        return
    if not isinstance(value, str) or re.fullmatch(r"[0-9A-Fa-f]{2}", value) is None:
        hint = (
            " — quote it, or YAML reads it as a number"
            if isinstance(value, int) and not isinstance(value, bool)
            else ""
        )
        errors.append(
            f'{label}: session_mode must be one quoted hex byte (e.g. "03" or "C0"), '
            f"got {value!r}{hint}"
        )
        return
    block = session_mode_block(
        int(value, 16), protocol, allowed_session_modes(profile_meta, protocol)
    )
    if block is None:
        return
    if block.hard:
        errors.append(f"{label}: session_mode {value} is refused — never allowed: {block.reason}")
    else:
        warnings.append(
            f"{label}: session_mode {value} is refused at runtime ({block.reason}) unless the "
            "profile declares a quirk that permits it for this ECU's protocol"
        )


def validate_iocontrol(
    ecu_def, path, ecu_name, allowed_states_set, errors, warnings, stats, profile_meta=None
) -> None:
    """Validate an ECU's ``iocontrol:`` block."""
    from canair.uds.protocol import classify_protocol

    iocontrol = ecu_def.get("iocontrol")
    if iocontrol is None:
        return
    if not isinstance(iocontrol, dict):
        errors.append(f"{path.name}/{ecu_name}: 'iocontrol' must be a dict")
        return
    identity = ecu_def.get("identity")
    protocol = classify_protocol(
        identity.get("id_protocol") if isinstance(identity, dict) else None
    )

    valid_fields = {
        "label",
        "verified",
        "on",
        "off",
        "release",
        "notes",
        "session",
        "session_mode",
        "hold",
        "vehicle_states",
        "status_param",
        True,
        False,  # YAML bool keys from unquoted on/off
    }
    for did, did_def in iocontrol.items():
        did_label = f"{path.name}/{ecu_name}/iocontrol/{did}"
        if isinstance(did, int) and len(str(did)) != 4:
            warnings.append(
                f"{did_label}: key parsed as the integer {did} — quote it; YAML reads a "
                'leading-zero key such as 0700 as octal (write "0700":)'
            )
        if not isinstance(did_def, dict):
            errors.append(f"{did_label}: must be a dict")
            continue
        if "label" not in did_def:
            warnings.append(f"{did_label}: missing 'label'")
        if "availability" in did_def:
            errors.append(
                f"{did_label}: legacy field 'availability' — renamed to vehicle_states "
                "(run scripts/migrate_states_status.py)"
            )
        for field in did_def:
            if field not in valid_fields:
                warnings.append(f"{did_label}: unknown field '{field}'")
        _validate_session_mode(
            did_def.get("session_mode"), did_label, errors, warnings, protocol, profile_meta
        )
        _validate_iocontrol_release(did_def, did_label, protocol, errors, warnings)
        validate_state_list(
            did_def.get("vehicle_states"), did_label, "vehicle_states", errors, allowed_states_set
        )
        stats["iocontrol"] += 1


def _validate_iocontrol_release(did_def, did_label, protocol, errors, warnings) -> None:
    """Check an IOControl entry's ``release:`` against its ON request.

    The release is what `canair io` sends to hand an output back, so a release that
    addresses another output, or repeats the ON request, would leave it driven. A
    KWP2000 ``0x30`` ON without one is refused at run time; say so here too.
    """
    from canair.safety.actuation import (
        default_release,
        normalize_request,
        on_refusal,
        parse_io_request,
        release_mismatch,
    )

    declared = did_def.get("release")
    on = str(did_def.get("on") or did_def.get(True) or "")
    if declared not in (None, ""):
        if not isinstance(declared, str):
            errors.append(f"{did_label}: release must be a quoted hex request, got {declared!r}")
            return
        try:
            release = normalize_request(declared)
        except ValueError as exc:
            errors.append(f"{did_label}: {exc}")
            return
        mismatch = release_mismatch(on, release) if on else None
        if mismatch:
            errors.append(f"{did_label}: {mismatch}")
            return
        io = parse_io_request(release)
        if (
            io is not None
            and io.service == INPUT_OUTPUT_CONTROL_BY_IDENTIFIER
            and io.option != IoControlParameter.RETURN_CONTROL_TO_ECU
        ):
            warnings.append(
                f"{did_label}: release {release} is not returnControlToECU "
                f"(2F{io.identifier.hex().upper()}00)"
            )
        return
    if on:
        reason = on_refusal(on, default_release(on, protocol), protocol)
        if reason:
            warnings.append(f"{did_label}: `canair io` refuses its ON — {reason}")
