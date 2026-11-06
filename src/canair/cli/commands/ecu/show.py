"""``canair ecu [ECU] [VIEW]``: list the ECUs, or show one."""

from __future__ import annotations

from canair.analysis.ecu_summary import list_records, pids_def_for_tx
from canair.ecus.names import resolve_tx
from canair.ecus.pids import load_pids
from canair.ecus.registry import load_ecus
from canair.profile import active
from canair.render import messages

from .detail import cmd_detail
from .edit import cmd_edit
from .list_view import cmd_list
from .pids_view import cmd_pids
from .records import detail_record


def unknown_ecu(value: str, records: list[dict]) -> int:
    messages.error(f"unknown ECU {value!r}")
    messages.hint("available ECUs: " + ", ".join(r["name"] for r in records))
    return 1


def run(args) -> int:
    from canair.ecus.can_buses import bus_names

    ecus = load_ecus(profile=active())
    pids_data = load_pids(profile=active())
    labels = bus_names(profile=active())

    if not args.ecu:
        records = list_records(ecus, pids_data, sort=getattr(args, "sort", "bus"), profile=active())
        if not records:
            messages.error("no ECUs found in the active profile")
            messages.hint("see `canair profile show`")
            return 1
        # Sorting by states implies showing the column (else the order is invisible).
        show_states = getattr(args, "states", False) or getattr(args, "sort", "bus") == "states"
        return cmd_list(records, args.json, show_states=show_states)

    tx_id = resolve_tx(args.ecu, profile=active())
    info = ecus.get(tx_id) if tx_id is not None else None
    if info is None:
        return unknown_ecu(args.ecu, list_records(ecus, pids_data, profile=active()))

    # info is only non-None when tx_id resolved (see the guarded .get above).
    assert tx_id is not None
    pids_name, ecu_def = pids_def_for_tx(pids_data, tx_id)

    if getattr(args, "view", None) == "pids":
        return cmd_pids(info, tx_id, ecu_def, args.json)

    if getattr(args, "view", None) == "edit":
        return cmd_edit(info, tx_id)

    rec = detail_record(info, tx_id, pids_name, ecu_def, bus_labels=labels)
    return cmd_detail(rec, args.json)
