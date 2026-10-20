"""Pure AutoPID vehicle-profile transforms (no device I/O, no argparse).

The library core behind ``canair wican autopid``: turn the active bundle's
grouped ``ecus/`` PID definitions into the upstream **Vehicle Profile** JSON
(``generate_profile``), convert that to the firmware's **device format** for
upload (``to_device_format``), and normalize a device's stored profile back to
grouped form for diffing (``normalize_device_profile``).

These are pure ``dict`` → ``dict`` transforms — the command module
(``commands/wican.py``) owns the HTTP upload/download, diff rendering, and
argparse; this module owns the shape math so it can be unit-tested and reused
without a device. Companion to :mod:`canlib.autopid_layout` (byte-layout
reconstruction).
"""

from __future__ import annotations

from collections import OrderedDict

from canlib.addressing import EcuAddress, resolve_ecu_address
from canlib.autopid_send_to import DEFAULT_TYPE, MQTT_TOPIC_TYPE, topic_problem
from canlib.pids import pid_status
from canlib.response_frames import stored_count
from canlib.transport.elm327_addressing import (
    AUTO,
    elm_id,
    flow_control_data,
    pinned_target,
    target_for,
)
from canlib.transport.elm327_frame_count import annotate_request, requestable


class ProfileGenerationError(Exception):
    """The ``ecus/`` definitions cannot be turned into a shippable AutoPID profile."""


class DuplicateParameterError(ProfileGenerationError):
    """Two shipped parameters share the same name across the AutoPID profile.

    Parameter names become distinct signals/entities on the device, so a
    collision means one silently shadows the other. We refuse to generate such
    a profile rather than ship an ambiguous one.
    """


class InvalidSendToError(ProfileGenerationError):
    """A shipped signal's ``send_to`` topic is malformed or shared with another signal.

    Two signals on one topic would interleave on a single stream, and a malformed
    topic is published verbatim by the firmware — so either is refused at generation.
    """


def make_pid_init(
    tx_id: int,
    session: bool = False,
    *,
    fc_header: int | None = None,
    rx_filter: int | str | None = None,
    extended: bool | None = None,
) -> str:
    """Generate a PID's AT init string: header, flow-control header, receive filter.

    ``fc_header`` defaults to ``tx_id``; ``rx_filter`` adds an ``ATCRA`` for an id, or
    a bare ``ATCRA`` (the adapter's own filter) for :data:`AUTO` (see
    :func:`generate_profile` for when). The addressing comes before an optional UDS
    extended diagnostic session request (``1003``), whose reply must pass the
    filter too. The session is only needed by ECUs that reject 22xx DID reads in
    the default session; on the Ioniq 2017, SKM is the known example. (IGPM was
    previously flagged here but its service-22 reads work fine in the default
    session — verified 2026-07-21.)
    """
    fc_id = tx_id if fc_header is None else fc_header
    init = f"ATSH{elm_id(tx_id, extended)};ATFCSH{elm_id(fc_id, extended)};"
    if rx_filter == AUTO:
        init += "ATCRA;"
    elif isinstance(rx_filter, int):
        init += f"ATCRA{elm_id(rx_filter, extended)};"
    if session:
        init += "1003;"
    return init


def with_flow_control(init: str, fc_header: int, fc_data: str, extended: bool = False) -> str:
    """``init`` plus the prerequisites of flow-control mode 1, in the order it needs.

    The adapter rejects ``ATFCSM1`` until a flow-control header and data are set,
    and the firmware sends the top-level init only when the PID type changes, so
    the mode is switched on once here and each PID's init then re-points ``ATFCSH``.
    """
    base = init if not init or init.endswith(";") else init + ";"
    return f"{base}ATFCSH{elm_id(fc_header, extended)};ATFCSD{fc_data};ATFCSM1;"


def request_with_count(pid_code: object, pid_def: dict) -> str:
    """The AutoPID ``pid`` string, carrying an expected-response digit if earned.

    The firmware passes this string to its ELM327 co-processor verbatim
    (``strcpy`` + ``strcat("\\r")``, no validation), so a trailing count nibble
    reaches the adapter and lets it return the instant that many frames have
    arrived instead of sitting out its ``ATST96`` budget — ~614 ms per PID.

    The firmware, however, has no desync recovery: it accumulates into a single
    static buffer that is cleared only *after* a parse, so an undercount's queued
    tail silently prefixes the next PID's response forever. Hence a digit is only
    emitted for a count that :mod:`canlib.response_frames` recorded from verified
    wire evidence, and only when :func:`requestable` says the wire allows it.
    """
    request = str(pid_code)
    frames = stored_count(pid_def)
    if not requestable(request, frames):
        return request
    assert frames is not None
    return annotate_request(request, frames)


def generate_profile(
    data: dict, verified_only: bool = False, expected_responses: bool = False
) -> dict:
    """Generate Vehicle Profile format JSON (grouped parameters per PID).

    Produces the upstream source format where parameters is a dict of
    {"PARAM_NAME": "expression"} pairs. This format is used for:
    - The output JSON file (upstream PR-compatible)
    - Input to to_device_format() for upload to WiCAN

    The firmware does NOT accept this format directly — use to_device_format()
    to convert before uploading.

    ``expected_responses`` opts each eligible PID's request into the trailing
    frame-count digit — see :func:`request_with_count` for why it is opt-in.

    A PID entry whose shipped signals stream to their own MQTT topic carries a
    ``send_to`` map of ``{"SIGNAL_NAME": "topic"}`` alongside ``parameters`` (see
    :mod:`canlib.autopid_send_to`); the key is omitted when no signal has one, so a
    profile without streams is unchanged.
    """
    profile = {
        "car_model": data["car_model"],
        "init": data["init"],
        "pids": [],
        "can_filters": [],
    }

    # Where each shipped parameter name was first seen, so a collision can name
    # both origins. Populated as we build; checked after the full pass so every
    # duplicate is reported at once. The same for send_to topics.
    name_origin: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    topic_origin: dict[str, str] = {}
    topic_errors: list[str] = []

    # Each shipped entry with its ECU's address, so the pid_init can be chosen once
    # every shipped ECU is known (see `_address_entries`).
    addressed: list[tuple[dict, EcuAddress, bool]] = []

    for ecu_name, ecu in data["ecus"].items():
        address = resolve_ecu_address(data, ecu)
        session = ecu.get("session", False)

        for pid_code, pid_data in (ecu.get("pids") or {}).items():
            # Only `active` PIDs ship to the device. draft (unshipped placeholder),
            # static (unchanging identity/cal) and ignored (dead) are all excluded
            # — this is the single gate, replacing the old enabled/static/ignored mix.
            if pid_status(pid_data) != "active":
                continue

            parameters = {}
            send_to = {}
            for param_name, param in pid_data["parameters"].items():
                if not param.get("enabled", True):
                    continue
                if verified_only and not param.get("verified", False):
                    continue
                parameters[param_name] = param["expression"]

                origin = f"{ecu_name} {pid_code}"
                if param_name in name_origin:
                    collisions.setdefault(param_name, [name_origin[param_name]]).append(origin)
                else:
                    name_origin[param_name] = origin

                if "send_to" not in param:
                    continue
                topic = param["send_to"]
                problem = topic_problem(topic)
                if problem:
                    topic_errors.append(f"  '{param_name}' ({origin}): send_to {problem}")
                elif topic in topic_origin:
                    topic_errors.append(
                        f"  '{param_name}' ({origin}): send_to '{topic}' is also used by "
                        f"'{topic_origin[topic]}'"
                    )
                else:
                    topic_origin[topic] = param_name
                    send_to[param_name] = topic

            if not parameters:
                continue

            entry = {
                "pid_init": "",  # set by _address_entries
                "pid": (
                    request_with_count(pid_code, pid_data) if expected_responses else str(pid_code)
                ),
                "enabled": True,
                "period": str(pid_data.get("period", 5000)),
                "parameters": parameters,
            }
            if send_to:
                entry["send_to"] = send_to
            profile["pids"].append(entry)
            addressed.append((entry, address, session))

    _address_entries(profile, addressed, flow_control_data(data.get("isotp")))

    if collisions:
        lines = [
            f"  '{name}' shipped by {', '.join(origins)}"
            for name, origins in sorted(collisions.items())
        ]
        raise DuplicateParameterError(
            "duplicate parameter name(s) in the AutoPID profile — each name must "
            "be unique across all shipped PIDs (they become distinct signals on "
            "the device):\n" + "\n".join(lines)
        )
    if topic_errors:
        raise InvalidSendToError(
            "invalid send_to topic(s) in the AutoPID profile — each shipped signal's "
            "topic must be well-formed and unique:\n" + "\n".join(topic_errors)
        )

    return profile


def _address_entries(
    profile: dict, addressed: list[tuple[dict, EcuAddress, bool]], fc_data: str
) -> None:
    """Set every entry's ``pid_init``, and the flow-control setup when it is needed.

    A profile of only conventional ECUs (ISO 15765-4 ``tx + 0x08``) keeps the
    header pair it always had, so its device diff is unchanged. If any shipped ECU
    needs its receive filter or flow-control id pinned, every entry pins its own:
    the firmware runs each PID's init verbatim and never resets the filter between
    PIDs, so a PID that left it alone would inherit the previous ECU's. A broadcast
    ECU then restores the auto filter instead (known limit: its multi-frame replies
    would get mode-1 flow control to the broadcast id).
    """
    pin = any(target_for(address).explicit for _, address, _ in addressed)
    for entry, address, session in addressed:
        target = pinned_target(address) if pin else target_for(address)
        entry["pid_init"] = make_pid_init(
            target.header,
            session=session,
            fc_header=target.fc_header,
            rx_filter=target.rx_filter if pin else None,
            extended=target.extended,
        )
    if pin:
        first = next(target_for(a) for _, a, _ in addressed if target_for(a).explicit)
        profile["init"] = with_flow_control(
            profile["init"], first.fc_header, fc_data, first.extended
        )


def to_device_format(profile: dict, data: dict | None = None) -> dict:
    """Convert grouped profile to the device's expected format for upload.

    The firmware (autopid.c load_all_pids()) expects:
      {"cars": [{"car_model": "...", "init": "...", "pids": [...]}]}

    Each PID entry must have parameters as an array of objects:
      [{"name": "SOC", "expression": "B09/2", "unit": "%", "class": "battery",
        "period": "5000", "min": "", "max": "", "type": "Default", "send_to": ""}]

    A signal named in the entry's ``send_to`` map is emitted with ``type:
    MQTT_Topic`` and its topic, so the firmware publishes it on every poll.

    The web UI's build system (cars.js process_profile) does this same conversion
    when building vehicle_profiles.json from upstream source files.

    Args:
        profile: Grouped profile from generate_profile() (dict-format parameters)
        data: Optional YAML data for looking up unit/class/min/max per parameter
    """
    # Build parameter metadata lookup from YAML if provided
    param_meta = {}
    if data:
        for ecu in data["ecus"].values():
            for pid_data in (ecu.get("pids") or {}).values():
                for param_name, param in pid_data.get("parameters", {}).items():
                    param_meta[param_name] = param

    device_profile = {
        "car_model": profile["car_model"],
        "init": profile["init"],
        "pids": [],
    }

    for pid_entry in profile["pids"]:
        send_to = pid_entry.get("send_to") or {}
        params_array = []
        for name, expression in pid_entry["parameters"].items():
            meta = param_meta.get(name, {})
            topic = send_to.get(name, "")
            params_array.append(
                {
                    "name": name,
                    "expression": expression,
                    "unit": meta.get("unit", ""),
                    "class": meta.get("ha_class", "none") or "none",
                    "period": pid_entry.get("period", "5000"),
                    "min": str(meta.get("min", "")) if meta.get("min", "") != "" else "",
                    "max": str(meta.get("max", "")) if meta.get("max", "") != "" else "",
                    "type": MQTT_TOPIC_TYPE if topic else DEFAULT_TYPE,
                    "send_to": topic,
                }
            )

        device_profile["pids"].append(
            {
                "pid_init": pid_entry["pid_init"],
                "pid": pid_entry["pid"],
                "enabled": pid_entry.get("enabled", True),
                "parameters": params_array,
            }
        )

    return {"cars": [device_profile]}


def normalize_device_profile(device_data: dict) -> dict:
    """Normalize the device format back to grouped Vehicle Profile format.

    The device stores whatever is POSTed to /store_car_data verbatim.
    Depending on how the profile was uploaded, parameters may be:
    - An array of objects (from our upload or the web UI): [{name, expression, ...}]
    - A dict (if someone uploaded upstream source format): {NAME: expression}

    In both cases, the web UI creates one PID entry per parameter (flat), but
    our upload groups multiple parameters per PID entry. This normalizes
    everything to grouped dict format for diffing.

    A signal the firmware will stream (``type: MQTT_Topic``) lands in the group's
    ``send_to`` map, mirroring :func:`generate_profile`; a ``send_to`` string under
    any other type is inert on the device and is dropped.
    """
    if device_data.get("cars"):
        car = device_data["cars"][0]
    else:
        car = device_data

    groups = OrderedDict()

    for entry in car.get("pids", []):
        key = (entry.get("pid_init", ""), entry.get("pid", ""))
        if key not in groups:
            groups[key] = {
                "pid_init": entry.get("pid_init", ""),
                "pid": entry.get("pid", ""),
                "enabled": entry.get("enabled", True),
                "period": "5000",
                "parameters": {},
                "send_to": {},
            }

        params = entry.get("parameters", {})

        if isinstance(params, list):
            # Array-of-objects format: [{name, expression, period, ...}]
            for param in params:
                name = param.get("name", "")
                expr = param.get("expression", "")
                if name:
                    groups[key]["parameters"][name] = expr
                    if param.get("type") == MQTT_TOPIC_TYPE:
                        groups[key]["send_to"][name] = param.get("send_to") or ""
                # Use period from first parameter if available
                if "period" in param and groups[key]["period"] == "5000":
                    groups[key]["period"] = str(param["period"])
        elif isinstance(params, dict):
            # Dict format: {NAME: expression}
            for name, expr in params.items():
                groups[key]["parameters"][name] = expr

    for group in groups.values():
        if not group["send_to"]:
            del group["send_to"]

    return {
        "car_model": car.get("car_model", ""),
        "init": car.get("init", ""),
        "pids": list(groups.values()),
        "can_filters": [],
    }
