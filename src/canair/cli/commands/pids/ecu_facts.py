"""``canair pids`` kinds that set a fact about an ECU."""

from __future__ import annotations

import argparse

from canair.cli.args.hexarg import parse_hex_arg
from canair.cli.commands._edit import add_edit_args, edit_scope, run_edit
from canair.cli.profile_context import active, target_profile
from canair.core import yaml_io
from canair.core.edit_errors import EditError
from canair.ecus.can_buses import allowed_can_buses
from canair.ecus.pids import normalize_session_mode
from canair.profile.model import profile_for_path
from canair.render import ansi
from canair.transport.isotp_params import ECU_ISOTP_FIELDS
from canair.yaml_edit.echo import echo_edit
from canair.yaml_edit.ecu import set_addressing, tx_key
from canair.yaml_edit.pids import (
    find_ecu_file,
    remove_identity_field,
    set_can_bus,
    set_ecu_isotp,
    set_identity_field,
    set_iocontrol_scan_ranges,
    set_wake,
    update_iocontrol_field,
)
from canair.yaml_edit.revisions import set_gate

from .common import ECU_HELP, apply_in_scope, int_or_clear

_ISOTP_FIELD_HELP = {
    "blocksize": "Flow-control BlockSize canair sends (0 = send all consecutive frames)",
    "rx_flowcontrol_timeout": "Milliseconds to wait for a flow-control frame",
    "rx_consecutive_frame_timeout": "Milliseconds to wait for the next consecutive frame",
}


def add_parsers(sub) -> None:
    """Register the kinds of this group under ``canair pids``."""
    si = sub.add_parser("set-identity", help="Set a curated identity field (e.g. notes)")
    si.add_argument("ecu", help=ECU_HELP)
    si.add_argument("field", help="Identity field name, e.g. notes or description")
    si.add_argument("value", help="New value (notes are stored as a folded block scalar)")
    add_edit_args(si)
    si.set_defaults(_pids_func=cmd_set_identity)

    ri = sub.add_parser("rm-identity", help="Remove an identity field from an ECU")
    ri.add_argument("ecu", help=ECU_HELP)
    ri.add_argument("field", help="Identity field name to drop, e.g. software")
    add_edit_args(ri)
    ri.set_defaults(_pids_func=cmd_rm_identity)

    scb = sub.add_parser("set-can-bus", help="Set the physical CAN bus segment(s) the ECU sits on")
    scb.add_argument("ecu", help=ECU_HELP)
    scb.add_argument(
        "codes",
        nargs="+",
        metavar="CODE",
        help="One or more bus codes from the profile's can_buses.yaml "
        "(Hyundai: B-CAN/P-CAN/C-CAN/MM-CAN/H-CAN/ALL); some ECUs span two, e.g. H-CAN P-CAN",
    )
    add_edit_args(scb)
    scb.set_defaults(_pids_func=cmd_set_can_bus)

    srv = sub.add_parser(
        "set-revisions",
        help="Gate a PID or signal to some of its ECU's revisions",
        description=(
            "Record that a PID (ECU:PID) or one signal of it (ECU:PID:SIGNAL) exists only on "
            "some revisions of its ECU (the ECU's revisions: IDs, declared with `canair ecu "
            "revisions`). A car whose ECU is another revision never reads it; one whose "
            "revision is undetermined skips it. A signal's revisions must be within its "
            "PID's. --clear: it applies to every revision (the default)."
        ),
    )
    srv.add_argument(
        "target", metavar="ECU:PID[:SIGNAL]", help="The PID, or one signal of it, to gate"
    )
    srv.add_argument(
        "revisions", nargs="*", metavar="REVISION", help="Revision IDs (or a comma list)"
    )
    srv.add_argument("--clear", action="store_true", help="Apply to every revision")
    srv.set_defaults(_pids_func=cmd_set_revisions)

    sir = sub.add_parser(
        "set-iocontrol-ranges",
        help="Set the IOControl (0x2F) scan DID ranges swept on this ECU",
        description="Set the ECU's iocontrol_scan_ranges: list — the "
        "'START-END' hex DID ranges the `canair scan iocontrol` sweep covers. "
        "When unset, ranges are derived from the ECU's known 2F/22 DID keys, "
        "else the full DID space (0000-FFFF). This replaces the old hardcoded "
        "HK body-controller zones with a per-ECU, profile-declared range.",
    )
    sir.add_argument("ecu", help=ECU_HELP)
    sir.add_argument(
        "ranges",
        nargs="+",
        metavar="RANGE",
        help="One or more 'START-END' hex DID ranges (e.g. B000-BFFF C000-C0FF)",
    )
    add_edit_args(sir)
    sir.set_defaults(_pids_func=cmd_set_iocontrol_ranges)

    sio = sub.add_parser(
        "set-iocontrol",
        help="Set fields of a curated IOControl actuator entry",
        description="Set fields of an existing iocontrol: entry (the actuators "
        "`canair io` drives). Writes only the fields given, adding any the entry "
        "lacks. Promote a scanner discovery first (the `canair io` TUI's P key).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair pids set-iocontrol IGPM BC01 --verified --label 'Low beam'\n"
        "  canair pids set-iocontrol BCM 6900 --session-mode C0 --no-hold\n"
        "  canair pids set-iocontrol HVAC F010 --on 2FF01003FF --off 2FF01000\n"
        "  canair pids set-iocontrol BCM 6900 --release 30690000\n"
        "  canair pids set-iocontrol ECM 0155 --enable-mask FF\n",
    )
    sio.add_argument("ecu", help=ECU_HELP)
    sio.add_argument("did", metavar="DID", help="The iocontrol: entry key (e.g. BC01)")
    sio.add_argument("--label", default=None, help="Human label for the actuator")
    sio.add_argument("--notes", default=None, help="Free-text note (replaces the existing one)")
    sio.add_argument(
        "--verified",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Mark as tested on this car (--no-verified to clear)",
    )
    sio.add_argument("--on", default=None, metavar="HEX", help="Full ON request (e.g. 2FBC0103)")
    sio.add_argument("--off", default=None, metavar="HEX", help="Full OFF request (e.g. 2FBC0100)")
    sio.add_argument(
        "--release",
        default=None,
        metavar="HEX",
        help="Full request that hands the output back to the ECU, sent on exit, on Ctrl+C "
        "and by --poll: IOControl on the same identifier, or 20 (StopDiagnosticSession) "
        "for a KWP2000 0x30 entry. UDS implies 2F{DID}00; a KWP2000 0x30 entry has no "
        "standard one, and `canair io` refuses its ON until this is set",
    )
    sio.add_argument(
        "--enable-mask",
        default=None,
        metavar="HEX",
        help="UDS only: the controlEnableMaskRecord a packeted DID's requests end with, when "
        "its maker uses the mask concept (ISO 14229-1). The value keys append it, and the "
        "implied release becomes 2F{DID}00 plus FF per mask byte",
    )
    sio.add_argument(
        "--session",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Open a diagnostic session before actuating",
    )
    sio.add_argument(
        "--session-mode",
        default=None,
        metavar="XX",
        help="DiagnosticSessionControl sub-function to open (default 03; an OEM "
        "session such as Nissan C0 also needs the profile quirk that permits it)",
    )
    sio.add_argument(
        "--hold",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Keep the session alive after ON until Ctrl+C, then send the release",
    )
    sio.add_argument(
        "--status-param",
        default=None,
        metavar="NAME",
        help="Signal read before and after actuating, as a best-effort readback",
    )
    add_edit_args(sio)
    sio.set_defaults(_pids_func=cmd_set_iocontrol)

    sw = sub.add_parser(
        "set-wake",
        help="Set how to rouse a fast-sleeping ECU before reads (wake ritual)",
        description="Declare a per-ECU wake ritual (canair.ecus.wake). Some modules "
        "(e.g. a Smart Key Module) power their CAN transceiver only briefly and "
        "sleep again within a second or two, so a single 10 01 wake races the "
        "sleep timer. `rapid_read` fires a cheap prime PID back-to-back to hold "
        "the transceiver awake, then opens a session — honoured by "
        "`session <ECU> --wake` on both transports.",
    )
    sw.add_argument("ecu", help=ECU_HELP)
    sw.add_argument(
        "--method",
        required=True,
        choices=["rapid_read", "session", "relay"],
        help="rapid_read = back-to-back primes (fast-sleepers); session = single 10 01; "
        "relay = rapid_read + iocontrol relay (Ioniq SKM, needs skm_wakeup quirk)",
    )
    sw.add_argument(
        "--prime-pid",
        default=None,
        metavar="REQ",
        help="Cheap full UDS request fired repeatedly to hold the ECU awake "
        "(SID-first, e.g. 22B003 / 1001 / 3E00; default 1001)",
    )
    sw.add_argument(
        "--attempts",
        type=int,
        default=None,
        metavar="N",
        help="Back-to-back prime frames (default 6)",
    )
    sw.add_argument(
        "--interval-ms",
        type=int,
        default=None,
        metavar="MS",
        help="Gap between primes in ms — must be under the ECU's sleep timer (default 60)",
    )
    sw.add_argument(
        "--sleep-timer-ms",
        type=int,
        default=None,
        metavar="MS",
        help="Documented time the ECU stays awake after a frame (informational)",
    )
    sw.add_argument(
        "--session-mode",
        default=None,
        metavar="XX",
        help="DiagnosticSessionControl sub-function entered after waking (default 03)",
    )
    sw.add_argument("--notes", default=None, help="Free-text note on the wake ritual")
    add_edit_args(sw)
    sw.set_defaults(_pids_func=cmd_set_wake)

    sad = sub.add_parser(
        "set-addressing",
        help="Set an ECU's CAN addressing override (mode / extension bytes / FC id / rx_id)",
        description="Set the make-specific CAN addressing knobs on an ECU: the "
        "addressing `mode` (11-bit vs the 29-bit modes / extended-11-bit), the "
        "ISO-TP extension bytes (`target_address`/`source_address`, for BMW/PSA "
        "extended-11-bit), a flow-control arbitration override (`fc_id`, for "
        "functional-TX / physical-RX ECUs like Renault/Mitsubishi), and/or the "
        "response-address `rx_id`. Writes only the fields given.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair pids set-addressing PCM --mode normal_fixed_29bit\n"
        "  canair pids set-addressing DME --mode normal_extended_11bit --target-address 0x12\n"
        "  canair pids set-addressing EVC --mode normal_29bit --fc-id 0x18DADBF1\n"
        "  canair pids set-addressing BMS --rx-id 0x784\n",
    )
    sad.add_argument("ecu", help=ECU_HELP)
    sad.add_argument(
        "--mode",
        help="Addressing mode (normal_11bit | normal_29bit | normal_fixed_29bit | "
        "normal_extended_11bit | extended_29bit)",
    )
    sad.add_argument(
        "--target-address",
        dest="target_address",
        help="ISO-TP target extension byte (hex, e.g. 0x12) — extended-11-bit/29-bit modes",
    )
    sad.add_argument(
        "--source-address",
        dest="source_address",
        help="ISO-TP tester (source) byte (hex, default 0xF1) — extended-11-bit modes",
    )
    sad.add_argument(
        "--fc-id",
        dest="fc_id",
        help="Flow-control arbitration override (hex) — functional-TX / physical-RX ECUs",
    )
    sad.add_argument(
        "--rx-id",
        dest="rx_id",
        help="CAN response-address override (hex, e.g. 0x784)",
    )
    add_edit_args(sad)
    sad.set_defaults(_pids_func=cmd_set_addressing)

    sis = sub.add_parser(
        "set-isotp",
        help="Set an ECU's own ISO-TP flow control (slcan-tcp), over the profile's",
        description="Set or clear an ECU's own isotp: fields, laid over the profile's on "
        "slcan-tcp: for an ECU that sends no consecutive frame after BlockSize 0.",
        epilog="example: canair pids set-isotp EWP --blocksize 8 (or --blocksize clear)",
    )
    sis.add_argument("ecu", help=ECU_HELP)
    for field in ECU_ISOTP_FIELDS:
        sis.add_argument(
            f"--{field.replace('_', '-')}",
            dest=field,
            metavar="N|clear",
            help=f"{_ISOTP_FIELD_HELP[field]} (a number, or clear to inherit the profile's)",
        )
    add_edit_args(sis)
    sis.set_defaults(_pids_func=cmd_set_isotp)


def cmd_set_identity(args: argparse.Namespace) -> int:
    return run_edit(
        lambda: set_identity_field(
            args.ecu, args.field, args.value, pids_dir=args.dir, profile=target_profile(args)
        ),
        f"{args.ecu} identity.{args.field} updated",
        args=args,
    )


def cmd_rm_identity(args: argparse.Namespace) -> int:
    return run_edit(
        lambda: remove_identity_field(
            args.ecu, args.field, pids_dir=args.dir, profile=target_profile(args)
        ),
        f"{args.ecu} identity.{args.field} removed",
        args=args,
    )


def cmd_set_can_bus(args: argparse.Namespace) -> int:
    # Resolve the vocabulary from the profile owning the target ecus/ dir
    # (or the active profile). Enforced only when the profile declares one.
    allowed = allowed_can_buses(profile_for_path(args.dir) if args.dir else active())
    if allowed:
        bad = [c for c in args.codes if c not in allowed]
        if bad:
            raise EditError(
                f"unknown CAN bus code(s) {bad} — declared in can_buses.yaml: {sorted(allowed)}"
            )

    return run_edit(
        lambda: set_can_bus(args.ecu, args.codes, pids_dir=args.dir, profile=target_profile(args)),
        f"{args.ecu} can_bus -> [{', '.join(args.codes)}]",
        args=args,
    )


def cmd_set_revisions(args: argparse.Namespace) -> int:
    parts = args.target.split(":")
    if len(parts) not in (2, 3) or not all(p.strip() for p in parts):
        raise EditError(f"expected ECU:PID or ECU:PID:SIGNAL, got {args.target!r}")
    ecu, pid, signal = parts[0], parts[1], (parts[2] if len(parts) == 3 else None)
    revs = [r for part in args.revisions for r in part.split(",") if r.strip()]
    if args.clear == bool(revs):
        raise EditError("give the revisions, or --clear")
    with edit_scope(args):
        fpath = set_gate(ecu, pid, signal, None if args.clear else revs, profile=active())
    what = f"[{', '.join(revs)}]" if revs else "every revision (cleared)"
    echo_edit(f"{args.target} revisions -> {what}", fpath)
    return 0


def cmd_set_iocontrol_ranges(args: argparse.Namespace) -> int:
    return run_edit(
        lambda: set_iocontrol_scan_ranges(
            args.ecu, args.ranges, pids_dir=args.dir, profile=target_profile(args)
        ),
        f"{args.ecu} iocontrol_scan_ranges -> [{', '.join(args.ranges)}]",
        args=args,
    )


def cmd_set_iocontrol(args: argparse.Namespace) -> int:
    import re

    fields: dict[str, str | bool] = {
        name: value
        for name, value in (
            ("label", args.label),
            ("notes", args.notes),
            ("verified", args.verified),
            ("on", args.on),
            ("off", args.off),
            ("release", args.release),
            ("enable_mask", args.enable_mask),
            ("session", args.session),
            ("session_mode", args.session_mode),
            ("hold", args.hold),
            ("status_param", args.status_param),
        )
        if value is not None
    }
    if not fields:
        raise EditError("nothing to set — pass at least one field")
    if args.session_mode is not None:
        try:
            fields["session_mode"] = normalize_session_mode(args.session_mode)
        except ValueError as exc:
            raise EditError(f"--session-mode: {exc}") from None
    for name in ("on", "off", "release", "enable_mask"):
        value = fields.get(name)
        if isinstance(value, str) and value and not re.fullmatch(r"(?:[0-9A-Fa-f]{2})+", value):
            flag = name.replace("_", "-")
            raise EditError(f"--{flag} expects hex bytes (e.g. 2FBC0103), got {value!r}")

    def do():
        for name, value in fields.items():
            update_iocontrol_field(
                args.ecu, args.did, name, value, pids_dir=args.dir, profile=target_profile(args)
            )

    fpath = apply_in_scope(args, do)
    detail = ", ".join(f"{k}={v}" for k, v in fields.items() if k != "notes")
    echo_edit(f"{args.ecu} iocontrol {args.did.upper()} -> {{{detail}}}", fpath)
    return 0


def cmd_set_wake(args: argparse.Namespace) -> int:
    fields: dict = {"method": args.method}
    if args.prime_pid is not None:
        fields["prime_pid"] = args.prime_pid
    if args.attempts is not None:
        fields["attempts"] = args.attempts
    if args.interval_ms is not None:
        fields["interval_ms"] = args.interval_ms
    if args.sleep_timer_ms is not None:
        fields["sleep_timer_ms"] = args.sleep_timer_ms
    if args.session_mode is not None:
        fields["session_mode"] = args.session_mode
    if args.notes is not None:
        fields["notes"] = args.notes

    detail = ", ".join(f"{k}={v}" for k, v in fields.items() if k != "notes")
    return run_edit(
        lambda: set_wake(args.ecu, fields, pids_dir=args.dir, profile=target_profile(args)),
        f"{args.ecu} wake -> {{{detail}}}",
        args=args,
    )


def cmd_set_isotp(args: argparse.Namespace) -> int:
    values = {f: int_or_clear(v) for f in ECU_ISOTP_FIELDS if (v := getattr(args, f)) is not None}
    detail = ", ".join(f"{k}={'cleared' if v is None else v}" for k, v in values.items())
    run = lambda: set_ecu_isotp(args.ecu, values, pids_dir=args.dir, profile=target_profile(args))  # noqa: E731
    return run_edit(run, f"{args.ecu} isotp -> {{{detail}}}", args=args)


def cmd_set_addressing(args: argparse.Namespace) -> int:
    if not any(
        getattr(args, k) is not None
        for k in ("mode", "target_address", "source_address", "fc_id", "rx_id")
    ):
        raise EditError(
            "nothing to set — pass at least one of --mode/--target-address/--source-address/--fc-id/--rx-id"
        )

    fpath = find_ecu_file(args.ecu, pids_dir=args.dir, profile=target_profile(args))
    doc = yaml_io.safe_load(fpath.read_text()) or {}
    tx_id = next(
        (int(d["tx_id"]) for d in doc.values() if isinstance(d, dict) and "tx_id" in d),
        None,
    )
    if tx_id is None:
        raise EditError(f"no tx_id found in {fpath}")

    with edit_scope(args):
        changed = set_addressing(
            tx_id,
            mode=args.mode,
            target_address=parse_hex_arg(args.target_address, "target-address"),
            source_address=parse_hex_arg(args.source_address, "source-address"),
            fc_id=parse_hex_arg(args.fc_id, "fc-id"),
            rx_id=parse_hex_arg(args.rx_id, "rx-id"),
            ecus_dir=args.dir,
            profile=target_profile(args),
        )

    disp = tx_key(tx_id)
    if changed:
        echo_edit(f"{args.ecu} addressing updated ({disp})", fpath)
    else:
        print(
            f"{ansi.DIM}  {args.ecu} ({disp}) addressing already as requested; nothing to change.{ansi.RESET}"
        )
    return 0
