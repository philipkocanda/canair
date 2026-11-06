"""What ``canair ecu`` shows, as data: per-ECU stats, list and detail records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from canair.analysis import ecu_summary
from canair.cli.profile_context import active
from canair.ecus.registry import rx_addr_str
from canair.profile.model import ProfileError

# Identity fields to surface in the detail view, in display order.
# (name/alias/description/id_protocol are handled separately in the header.)
IDENTITY_FIELDS = [
    ("part_number", "Part number"),
    ("supplier", "Supplier"),
    ("mfg_date", "Mfg date"),
    ("hw_version", "HW version"),
    ("sw_version", "SW version"),
    ("hw_sw", "HW/SW"),
    ("boot_sw", "Boot SW"),
    ("app_sw", "App SW"),
    ("fw_version", "FW version"),
    ("firmware", "Firmware"),
    ("calibration", "Calibration"),
    ("ecu_id", "ECU id"),
    ("sw_id", "SW id"),
    ("serial", "Serial"),
    ("diag_address", "Diag addr"),
    ("vin", "VIN"),
]


def detail_record(
    info: Mapping[str, Any],
    tx_id: int,
    pids_name: str | None,
    ecu_def: dict | None,
    bus_labels: dict | None = None,
) -> dict:
    name = info.get("name") or f"0x{tx_id:03X}"
    bus_labels = bus_labels or {}
    can_bus = info.get("can_bus")
    rec = {
        "name": name,
        "alias": info.get("alias"),
        "description": info.get("description", ""),
        "id_protocol": info.get("id_protocol"),
        "can_bus": can_bus,
        "can_bus_labels": [bus_labels.get(c, c) for c in (can_bus or [])],
        "tx": f"0x{tx_id:03X}",
        "rx": rx_addr_str(tx_id, profile=active()),
        "notes": info.get("notes"),
        "identity": {k: info[k] for k, _ in IDENTITY_FIELDS if info.get(k) is not None},
        "identity_note": _identity_note(name),
    }
    rec["dtcs"] = _dtc_status(tx_id)
    per_pid, total = ecu_summary.captures_by_pid(name, profile=active())
    if ecu_def is not None:
        rec["stats"] = ecu_summary.pid_stats(ecu_def)
        rec["vehicle_states"] = ecu_def.get("vehicle_states")
        rec["captures"] = total
        rec["pid_list"] = ecu_summary.pid_details(ecu_def, per_pid)
    else:
        rec["stats"] = None
        rec["captures"] = total
        rec["pid_list"] = []
    return rec


def _dtc_status(tx_id: int) -> dict | None:
    """The ECU's codes after its latest logged reading or clear on this car (None: none)."""
    from canair.dtc.history import ecu_status
    from canair.dtc.refs import ecu_ref

    try:
        status = ecu_status(ecu_ref(tx_id, profile=active()), profile=active())
    except (ProfileError, OSError):
        return None
    return status.as_dict() if status is not None else None


def _identity_note(name: str) -> str | None:
    """Which revision the recorded identity belongs to, when the car's ECU may be another.

    The part number, calibration and the like were read from the ECU's reference revision;
    the loader withholds them from another revision, so say why they are absent rather than
    leave the identity looking merely unrecorded.
    """
    from canair.ecus.pids import load_raw_pids
    from canair.ecus.revisions import determined_revisions
    from canair.variants.revisions import canonical_ecu, identity_reference

    raw = load_raw_pids(profile=active()).get("ecus") or {}
    key = canonical_ecu(name, raw)
    reference = identity_reference(raw[key]) if key else None
    if reference is None:
        return None
    revision = determined_revisions(active()).get(key)
    if revision is None:
        return f"Recorded from the {reference} revision; this car's revision is undetermined."
    if revision != reference:
        return f"None recorded for the {revision} revision (the recorded values belong to {reference})."
    return None
