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

from canlib.pids import pid_status
from canlib.response_frames import stored_count
from canlib.transport.elm327_frame_count import annotate_request, requestable


class DuplicateParameterError(Exception):
    """Two shipped parameters share the same name across the AutoPID profile.

    Parameter names become distinct signals/entities on the device, so a
    collision means one silently shadows the other. We refuse to generate such
    a profile rather than ship an ambiguous one.
    """


def make_pid_init(tx_id: int, session: bool = False) -> str:
    """Generate AT header init string from TX ID.

    If session=True, prepend a UDS extended diagnostic session request (10 03)
    before setting headers. This is only needed by ECUs that reject 22xx DID
    reads in the default session; on the Ioniq 2017, SKM is the known example.
    (IGPM was previously flagged here but its service-22 reads work fine in the
    default session — verified 2026-07-21.)
    """
    hex_id = f"{tx_id:03X}"
    init = f"ATSH{hex_id};ATFCSH{hex_id};"
    if session:
        init += "1003;"
    return init


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
    """
    profile = {
        "car_model": data["car_model"],
        "init": data["init"],
        "pids": [],
        "can_filters": [],
    }

    # Where each shipped parameter name was first seen, so a collision can name
    # both origins. Populated as we build; checked after the full pass so every
    # duplicate is reported at once.
    name_origin: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}

    for ecu_name, ecu in data["ecus"].items():
        tx_id = ecu["tx_id"]
        session = ecu.get("session", False)
        pid_init = make_pid_init(tx_id, session=session)

        for pid_code, pid_data in (ecu.get("pids") or {}).items():
            # Only `active` PIDs ship to the device. draft (unshipped placeholder),
            # static (unchanging identity/cal) and ignored (dead) are all excluded
            # — this is the single gate, replacing the old enabled/static/ignored mix.
            if pid_status(pid_data) != "active":
                continue

            parameters = {}
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

            if not parameters:
                continue

            profile["pids"].append(
                {
                    "pid_init": pid_init,
                    "pid": (
                        request_with_count(pid_code, pid_data)
                        if expected_responses
                        else str(pid_code)
                    ),
                    "enabled": True,
                    "period": str(pid_data.get("period", 5000)),
                    "parameters": parameters,
                }
            )

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

    return profile


def to_device_format(profile: dict, data: dict | None = None) -> dict:
    """Convert grouped profile to the device's expected format for upload.

    The firmware (autopid.c load_all_pids()) expects:
      {"cars": [{"car_model": "...", "init": "...", "pids": [...]}]}

    Each PID entry must have parameters as an array of objects:
      [{"name": "SOC", "expression": "B09/2", "unit": "%", "class": "battery",
        "period": "5000", "min": "", "max": "", "type": "Default", "send_to": ""}]

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
        params_array = []
        for name, expression in pid_entry["parameters"].items():
            meta = param_meta.get(name, {})
            params_array.append(
                {
                    "name": name,
                    "expression": expression,
                    "unit": meta.get("unit", ""),
                    "class": meta.get("ha_class", "none") or "none",
                    "period": pid_entry.get("period", "5000"),
                    "min": str(meta.get("min", "")) if meta.get("min", "") != "" else "",
                    "max": str(meta.get("max", "")) if meta.get("max", "") != "" else "",
                    "type": "Default",
                    "send_to": "",
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
            }

        params = entry.get("parameters", {})

        if isinstance(params, list):
            # Array-of-objects format: [{name, expression, period, ...}]
            for param in params:
                name = param.get("name", "")
                expr = param.get("expression", "")
                if name:
                    groups[key]["parameters"][name] = expr
                # Use period from first parameter if available
                if "period" in param and groups[key]["period"] == "5000":
                    groups[key]["period"] = str(param["period"])
        elif isinstance(params, dict):
            # Dict format: {NAME: expression}
            for name, expr in params.items():
                groups[key]["parameters"][name] = expr

    return {
        "car_model": car.get("car_model", ""),
        "init": car.get("init", ""),
        "pids": list(groups.values()),
        "can_filters": [],
    }
