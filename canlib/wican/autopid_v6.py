"""The AutoPID table for WiCAN firmware v6, from what :func:`ship_profile` says ships.

Firmware v6 replaced the 4.x ``{"cars": [...]}`` vehicle profile with a table document,
``{"groups", "pids", "filters"}``, accepted by ``PUT /api/autopid/config`` and parsed by
``ap_config_parse`` (meatpi-components ``components/autopid/autopid_config.c``). The
expressions carry over unchanged: from ``6.0.0-beta.3`` v6 indexes a reply as 4.x does,
the ISO-TP framing bytes counted. What changes is the container:

- each PID is a row with ``name``/``type``/``cmd``/``init``/``rxheader``/``group``/
  numeric ``period_ms``, its signals a ``parameters`` list with numeric bounds;
- the receive filter moves out of the init chain into ``rxheader``, which the firmware
  sends as ``ATCRA<id>`` after the row's init, and clears for the next row without one;
- the car-level init is no longer in the table: it is the device's ``specific_init``
  (and the car's name its ``vehicle``), so the document carries both as extra top-level
  keys, which the firmware's parser ignores;
- ``ATH0``/``ATS0``/``ATE1`` are dropped from every chain, as the firmware drops them (its
  poller reads the chip with headers and spaces on).

The firmware copies strings into fixed buffers without complaint, so an overlong value
would be truncated on the device; this module refuses one instead. Design and firmware
citations: ``plans/2026-10-31-wican-fw-v6-autopid-profile.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from canlib.expression.lint import firmware_grammar_problems
from canlib.transport.elm327_addressing import elm_id
from canlib.wican.autopid_class import autopid_class
from canlib.wican.autopid_profile import (
    ProfileGenerationError,
    ShippedPid,
    ShippedProfile,
    make_pid_init,
)

ROW_TYPE = "specific"
GROUP = "default"

# Usable bytes per string field: the firmware's buffer minus its terminating NUL
# (autopid_private.h AP_*_LEN). Bytes, not characters: "°C" is three.
FIELD_BYTES: dict[str, int] = {
    "name": 31,
    "cmd": 23,
    "init": 95,
    "rxheader": 15,
    "expression": 95,
    "unit": 15,
    "class": 23,
}
SPECIFIC_INIT_BYTES = 95  # the autopid setting, sized AP_INIT_LEN - 1
VEHICLE_BYTES = 63  # the autopid `vehicle` setting

# Table bounds (autopid_private.h, autopid_http.c): a table over any is refused whole.
MAX_PIDS = 512
MAX_PARAMS = 2048
MAX_PARAMS_PER_PID = 256
MAX_BODY_BYTES = 256 * 1024

# Commands the firmware's EEPROM guard refuses (obd_chip_guard.h): a table carrying
# one is rejected. ATPPS, the read-only summary, passes.
_EEPROM_WRITES = ("ATSD", "ATCV", "STWBR", "STSAVCAL")
# Print-format commands the firmware drops from every init chain.
_DROPPED_BY_FIRMWARE = frozenset({"ATH0", "ATS0", "ATE1"})


class V6LimitError(ProfileGenerationError):
    """The profile does not fit the v6 table: a field, a bound or a refused command."""


@dataclass(frozen=True)
class V6Profile:
    """The v6 table document, and the 4.x features it could not carry."""

    document: dict
    dropped_streams: tuple[str, ...]  # signals whose send_to v6 has no field for


def to_v6(shipped: ShippedProfile) -> V6Profile:
    """Render ``shipped`` as a v6 table document; raise :class:`V6LimitError` if it can't fit.

    Every problem is reported at once.
    """
    rows = [_row(pid) for pid in shipped.pids]
    document = {
        "vehicle": shipped.car_model,
        "specific_init": _chain(shipped.init),
        "groups": [],
        "pids": rows,
        "filters": [],
    }
    problems = _problems(document)
    if problems:
        raise V6LimitError(
            "the AutoPID profile does not fit firmware v6's table:\n"
            + "\n".join(f"  {p}" for p in problems)
        )
    dropped = tuple(name for pid in shipped.pids for name in pid.send_to)
    return V6Profile(document=document, dropped_streams=dropped)


def _row(pid: ShippedPid) -> dict:
    address = pid.addressing
    assert address is not None  # ship_profile addresses every PID
    rx_filter = address.rx_filter
    return {
        "name": f"{pid.ecu}_{pid.pid}",
        "type": ROW_TYPE,
        "cmd": pid.request,
        "init": _chain(
            make_pid_init(
                address.header,
                session=address.session,
                fc_header=address.fc_header,
                extended=address.extended,
            )
        ),
        # A row without a filter gets the adapter's own back from the firmware.
        "rxheader": elm_id(rx_filter, address.extended) if isinstance(rx_filter, int) else "",
        "group": GROUP,
        "period_ms": _period_ms(pid),
        "enabled": True,
        "parameters": [_parameter(name, param) for name, param in pid.params.items()],
    }


def _parameter(name: str, param: dict) -> dict:
    out: dict = {
        "name": name,
        "expression": param["expression"],
        "unit": param.get("unit", "") or "",
        "class": autopid_class(param),
    }
    # Numbers, or the firmware ignores them. On a specific row a reading outside
    # them is dropped, as 4.x did with the string bounds it parsed.
    for key in ("min", "max"):
        bound = _number(param.get(key))
        if bound is not None:
            out[key] = bound
    return out


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, str) and value.strip():
        try:
            number = float(value)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    return None


def _period_ms(pid: ShippedPid) -> int:
    """The PID's period as the number v6 needs; 0 would mean "the group's period"."""
    try:
        period = int(float(str(pid.period)))
    except ValueError:
        raise V6LimitError(
            f"{pid.ecu} {pid.pid}: period {pid.period!r} is not a number of milliseconds"
        ) from None
    if period < 1:
        raise V6LimitError(f"{pid.ecu} {pid.pid}: period must be at least 1 ms, not {period}")
    return period


def _chain(init: str) -> str:
    """``init`` as v6 runs it: ``;``-separated, without the commands it drops."""
    elements = [e.strip() for e in init.split(";")]
    return ";".join(
        e for e in elements if e and e.replace(" ", "").upper() not in _DROPPED_BY_FIRMWARE
    )


def _eeprom_writes(chain: str) -> list[str]:
    found = []
    for element in chain.split(";"):
        command = element.replace(" ", "").upper()
        if command.startswith("ATPP") and not command.startswith("ATPPS"):
            found.append(element)
        elif command.startswith(_EEPROM_WRITES):
            found.append(element)
    return found


def _too_long(where: str, field: str, value: str, limit: int) -> str | None:
    size = len(value.encode())
    if size <= limit:
        return None
    return f"{where}: {field} is {size} bytes, over the {limit} the firmware keeps: {value!r}"


def _problems(document: dict) -> list[str]:
    problems: list[str] = []

    def check(problem: str | None) -> None:
        if problem:
            problems.append(problem)

    check(_too_long("vehicle", "name", document["vehicle"], VEHICLE_BYTES))
    check(_too_long("car init", "specific_init", document["specific_init"], SPECIFIC_INIT_BYTES))
    for command in _eeprom_writes(document["specific_init"]):
        problems.append(f"car init: {command!r} writes the chip's EEPROM; v6 refuses it")

    rows = document["pids"]
    if len(rows) > MAX_PIDS:
        problems.append(f"{len(rows)} PIDs, over the firmware's {MAX_PIDS}")
    total = sum(len(row["parameters"]) for row in rows)
    if total > MAX_PARAMS:
        problems.append(f"{total} signals, over the firmware's {MAX_PARAMS}")

    for row in rows:
        where = row["name"]
        for field in ("name", "cmd", "init", "rxheader"):
            check(_too_long(where, field, row[field], FIELD_BYTES[field]))
        for chain in (row["init"], row["cmd"]):
            for command in _eeprom_writes(chain):
                problems.append(f"{where}: {command!r} writes the chip's EEPROM; v6 refuses it")
        if len(row["parameters"]) > MAX_PARAMS_PER_PID:
            problems.append(
                f"{where}: {len(row['parameters'])} signals, over the firmware's "
                f"{MAX_PARAMS_PER_PID} per PID"
            )
        for param in row["parameters"]:
            signal = f"{where} {param['name']}"
            for field in ("name", "expression", "unit", "class"):
                check(_too_long(signal, field, param[field], FIELD_BYTES[field]))
            for reason in firmware_grammar_problems(param["expression"]):
                problems.append(f"{signal}: expression {param['expression']!r}: {reason}")

    body = len(json.dumps(document, separators=(",", ":")).encode())
    if body > MAX_BODY_BYTES:
        problems.append(f"the table is {body} bytes, over the firmware's {MAX_BODY_BYTES}")
    return problems
