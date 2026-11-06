"""Dispatch of ``canair bix``: which view the flags ask for."""

from __future__ import annotations

from canair.profile import active
from canair.render import messages
from canair.uds.support_map import SupportMapError, declaration

from .annotate import annotate_payload
from .convert import parse_input, print_result
from .subfunction import resolve_sub_bytes
from .table import print_overview, print_table


def run(args) -> int:
    # --annotate resolves its own width from the payload (the strongest evidence),
    # so resolve_sub_bytes is only for the payload-less views below.
    if args.table:
        sub_bytes, _ = resolve_sub_bytes(args)
        print_table(
            sub_bytes,
            args.max,
            legend=True,
            show_torque=args.show_torque,
            show_obdb=args.show_obdb,
        )
        return 0

    if args.annotate:
        params = None
        support = None
        protocol = None
        if args.ecu:
            if not args.pid:
                messages.error("--ecu requires --pid")
                return 2
            from canair.ecus.names import canonical_ecu_name_safe
            from canair.ecus.pids import build_ecu_index, load_pids
            from canair.ecus.protocol import ecu_protocol

            ecu = canonical_ecu_name_safe(args.ecu, profile=active()).upper()
            pid = args.pid.upper()
            idx = build_ecu_index(load_pids(profile=active()))
            ecu_entry = idx.get(ecu)
            if ecu_entry is not None:
                protocol = ecu_protocol(ecu_entry["tx_id"], profile=active())
            pid_entry = (ecu_entry or {}).get("pids", {}).get(pid, {})
            params = pid_entry.get("parameters", {})
            try:
                support = declaration({"support_map": pid_entry.get("support_map")})
            except SupportMapError:
                support = None  # a malformed declaration is `validate`'s to report
            if not params:
                messages.note(f"no defined signals for {ecu} {pid} — showing unmapped overlay")
                params = {}
        elif args.pid:
            messages.error("--pid requires --ecu")
            return 2
        return annotate_payload(
            " ".join(args.annotate),
            args.sub_bytes,
            params,
            raw_frame=args.raw_frame,
            show_torque=args.show_torque,
            show_obdb=args.show_obdb,
            pid=args.pid,
            legend=args.legend,
            support=support,
            protocol=protocol,
        )

    if args.raw_frame:
        messages.error("--raw only applies to --annotate")
        return 2

    if not args.value:
        sub_bytes, _ = resolve_sub_bytes(args)
        print_overview(sub_bytes, show_torque=args.show_torque, show_obdb=args.show_obdb)
        return 0

    notation, idx = parse_input(args.value)
    sub_bytes, _ = resolve_sub_bytes(args)
    print_result(notation, idx, sub_bytes)
    return 0
