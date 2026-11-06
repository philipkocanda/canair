"""``canair pids`` kinds that set a PID's own fields."""

from __future__ import annotations

import argparse

from canair.cli.commands._edit import add_edit_args, run_edit
from canair.core.edit_errors import EditError, translate
from canair.ecus.pid_status import PID_STATUSES
from canair.ecus.pids import INHERITED_PARAM_FIELDS
from canair.profile import active
from canair.render import ansi
from canair.uds.support_map import MapDecl, SupportMapError
from canair.yaml_edit.echo import echo_edit
from canair.yaml_edit.pids import (
    find_ecu_file,
    hoist_defaults,
    set_pid_defaults,
    set_pid_notes,
    set_pid_period,
    set_pid_status,
    set_pid_variable_length,
    set_response_frames,
    set_sensitive,
    set_snapshot_only,
    set_support_map,
)

from .common import apply_in_scope, ecu_names, int_or_clear, words_verb


def add_parsers(sub) -> None:
    """Register the kinds of this group under ``canair pids``."""
    sps = sub.add_parser("set-pid-status", help="Set a PID's lifecycle status")
    sps.add_argument("ecu")
    sps.add_argument("pid")
    sps.add_argument("status", choices=list(PID_STATUSES))
    add_edit_args(sps)
    sps.set_defaults(_pids_func=cmd_set_pid_status)

    spv = sub.add_parser(
        "set-pid-variable-length",
        help="Flag a PID as returning legitimately variable-length responses",
    )
    spv.add_argument("ecu")
    spv.add_argument("pid")
    spv.add_argument(
        "value",
        choices=["true", "false"],
        help="true = variable-length (a short payload is not truncation, and no frame "
        "count is ever requested or recorded; drops any response_frames count); "
        "false = clear the flag (fixed-length, the default)",
    )
    add_edit_args(spv)
    spv.set_defaults(_pids_func=cmd_set_pid_variable_length)

    sso = sub.add_parser(
        "set-snapshot-only",
        help="Mark a DID as served only inside a DTC snapshot (decoded by canair dtc)",
        description=(
            "Record that the ECU serves this service-22 DID only inside a DTC snapshot: a live "
            "read draws an NRC, but the snapshot data `canair dtc` reads decodes with the PID's "
            "signals. A snapshot-only PID is never polled in a bare-ECU sweep and never "
            "shipped to the device. --length gives the DID's data length in a snapshot record, "
            "which a record holding several DIDs needs (a one-DID record is self-describing). "
            "--clear removes the mark and the length."
        ),
    )
    sso.add_argument("ecu")
    sso.add_argument("pid", help="The DID's read PID, e.g. 224710")
    sso.add_argument("--length", type=int, help="Data length in a snapshot record, in bytes")
    sso.add_argument("--clear", action="store_true", help="Remove snapshot_only and the length")
    add_edit_args(sso)
    sso.set_defaults(_pids_func=cmd_set_snapshot_only)

    sse = sub.add_parser(
        "set-sensitive",
        help="Mark a PID or signal as sensitive: masked on screen, never saved or shared",
        description=(
            "Mark a PID (or, with --param, one of its signals) whose value identifies the "
            "car or its owner like a VIN does — e.g. the CCM's EVCCID, which Autocharge "
            "chargers bill against. Its reply is shown as ***** (`canair read --reveal` "
            "shows it), never written to captures/, and left out of contribute, the blind "
            "strip, the docs explorer and the WiCAN AutoPID export. A sensitive signal also "
            "hides its PID's raw bytes, which give it away."
        ),
    )
    sse.add_argument("ecu")
    sse.add_argument("pid")
    sse.add_argument("value", choices=["true", "false"], help="true = sensitive; false = clear")
    sse.add_argument("--param", metavar="NAME", help="Flag this signal instead of the whole PID")
    add_edit_args(sse)
    sse.set_defaults(_pids_func=cmd_set_sensitive)

    ssm = sub.add_parser(
        "set-support-map",
        help="Declare where a PID's support map sits (which following bits/bytes are signals)",
        description=(
            "Record that the PID's data starts with a constant support map: LENGTH bytes at "
            "data offset AT (counted after the SID and DID echo) marking which of the data that "
            "follows are real signals: one map bit per data bit (--unit bit), per data byte "
            "(--unit byte), or per parameter of the record whatever its width (--unit field, "
            "the service-21 pages), most significant bit first. A field map may count from the "
            "least significant bit (--order lsb) and, in a record of more than 32 parameters, "
            "carry further bitmaps (--also-at). `canair investigate ECU --support-map` finds "
            "and scores candidates and prints the exact command; `canair coverage` then stops "
            "reporting bytes and bits the map says carry no signal. --clear removes the "
            "declaration."
        ),
    )
    ssm.add_argument("ecu")
    ssm.add_argument("pid")
    ssm.add_argument("--at", type=int, help="Data offset of the first map byte (usually 0)")
    ssm.add_argument("--length", type=int, help="Map length in bytes (1-4)")
    ssm.add_argument("--unit", choices=["bit", "byte", "field"], help="What one map bit marks")
    ssm.add_argument(
        "--order",
        choices=["msb", "lsb"],
        default="msb",
        help="Which map bit is the first (default msb; lsb only with --unit field)",
    )
    ssm.add_argument(
        "--also-at",
        metavar="OFFSETS",
        help="Comma-separated data offsets of further bitmaps (--unit field only)",
    )
    ssm.add_argument("--clear", action="store_true", help="Remove the declaration")
    add_edit_args(ssm)
    ssm.set_defaults(_pids_func=cmd_set_support_map)

    spp = sub.add_parser(
        "set-pid-period",
        help="Set how often the WiCAN AutoPID re-polls a PID (ms)",
        description=(
            "Set the PID's AutoPID polling interval in milliseconds. It applies only to "
            "the profile shipped to the device (`wican autopid write`); `monitor` and "
            "`read` poll every selector once per cycle regardless. 0 is refused because "
            "the firmware treats it as unset and substitutes a default."
        ),
    )
    spp.add_argument("ecu")
    spp.add_argument("pid")
    spp.add_argument(
        "value",
        metavar="MS|clear",
        help="interval in milliseconds (int >= 1), or 'clear' to remove it (device default 5000)",
    )
    add_edit_args(spp)
    spp.set_defaults(_pids_func=cmd_set_pid_period)

    srf = sub.add_parser(
        "set-response-frames",
        help="Set how many CAN frames a PID's response occupies (skips the adapter's wait)",
    )
    srf.add_argument("ecu")
    srf.add_argument("pid")
    srf.add_argument(
        "value",
        metavar="N|clear",
        help="frame count (int >= 1), or 'clear' to remove it. Normally maintained "
        "automatically by the live commands once a count is confirmed on the wire",
    )
    add_edit_args(srf)
    srf.set_defaults(_pids_func=cmd_set_response_frames)

    spn = sub.add_parser(
        "set-pid-notes",
        help="Set (or clear) a PID's free-text notes",
        description=(
            "Set the PID-level notes: — the record of what the page is and what is "
            "known about it. It goes stale as decoding progresses, so keep it "
            "current.\n\n"
            "Omit VALUE to clear the field. Short notes stay inline; longer ones "
            "become a word-wrapped folded block scalar. An existing note keeps its "
            "position; a new one is inserted above parameters:."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    spn.add_argument("ecu")
    spn.add_argument("pid")
    spn.add_argument(
        "value",
        nargs="?",
        help="New notes text (omit to clear the field)",
    )
    add_edit_args(spn)
    spn.set_defaults(_pids_func=cmd_set_pid_notes)

    spd = sub.add_parser(
        "set-pid-defaults",
        help="Set fields every parameter of a PID inherits (unit, min, max, verified, source…)",
        description=(
            "Set PID-level defaults: a field stated on the PID applies to every parameter "
            "that sets none of its own, so a group such as the BMS cell voltages states "
            "`unit: V`, `min`, `max` and `verified` once. A parameter's own value, including "
            "an explicit empty one, still wins. Notes are not shared: a PID's describe the "
            "page, a parameter's the signal. --clear removes a default. "
            "`hoist-defaults` finds repetition you already have."
        ),
    )
    spd.add_argument("ecu")
    spd.add_argument("pid")
    spd.add_argument("--unit")
    spd.add_argument("--min")
    spd.add_argument("--max")
    spd.add_argument("--verified", dest="verified", action="store_true", default=None)
    spd.add_argument("--unverified", dest="verified", action="store_false")
    spd.add_argument("--source", help="Where the page's decoding came from")
    spd.add_argument("--source-link", action="append", metavar="URL", help="Repeatable")
    spd.add_argument(
        "--clear",
        action="append",
        choices=list(INHERITED_PARAM_FIELDS),
        metavar="FIELD",
        help="Remove a PID-level default (repeatable): " + ", ".join(INHERITED_PARAM_FIELDS),
    )
    add_edit_args(spd)
    spd.set_defaults(_pids_func=cmd_set_pid_defaults)

    hp = sub.add_parser(
        "hoist-defaults",
        help="Move fields repeated across a PID's parameters up to the PID",
        description=(
            "Find PIDs whose parameters repeat the same unit, min, max, verified, "
            "source or source_links and state the value once on the PID instead. A field "
            "moves only when it changes no signal: every parameter must state its own "
            "non-empty value, and one value must be shared by more than half of them (the "
            "rest keep theirs as overrides). The result is verified before it is kept. Name "
            "an ECU (and PID) to narrow the sweep; with neither, the whole profile is "
            "processed. --dry-run lists what would move."
        ),
    )
    hp.add_argument("ecu", nargs="?", help="Limit to this ECU (default: every ECU)")
    hp.add_argument("pid", nargs="?", help="Limit to this PID")
    hp.add_argument("--dry-run", action="store_true", help="List what would move; write nothing")
    add_edit_args(hp)
    hp.set_defaults(_pids_func=cmd_hoist_defaults)


def cmd_set_pid_status(args: argparse.Namespace) -> int:
    return run_edit(
        lambda: set_pid_status(args.ecu, args.pid, args.status, pids_dir=args.dir),
        f"{args.ecu} {args.pid} status -> {args.status}",
        args=args,
    )


def cmd_set_pid_variable_length(args: argparse.Namespace) -> int:
    value = args.value == "true"

    state = "true" if value else "false (cleared)"
    return run_edit(
        lambda: set_pid_variable_length(args.ecu, args.pid, value, pids_dir=args.dir),
        f"{args.ecu} {args.pid} variable_length -> {state}",
        args=args,
    )


def cmd_set_snapshot_only(args: argparse.Namespace) -> int:
    value = not args.clear

    if not value:
        state = "cleared"
    else:
        state = "true" + (f", snapshot_length {args.length}" if args.length is not None else "")
    return run_edit(
        lambda: set_snapshot_only(args.ecu, args.pid, value, length=args.length, pids_dir=args.dir),
        f"{args.ecu} {args.pid} snapshot_only -> {state}",
        args=args,
    )


def cmd_set_sensitive(args: argparse.Namespace) -> int:
    value = args.value == "true"

    def do():
        set_sensitive(args.ecu, args.pid, value, param=args.param, pids_dir=args.dir)

    fpath = apply_in_scope(args, do)
    what = f"{args.ecu} {args.pid}" + (f" {args.param}" if args.param else "")
    state = "true" if value else "false (cleared)"
    echo_edit(f"{what} sensitive -> {state}", fpath)
    if value:
        from canair.captures.sensitive_purge import purge_dir
        from canair.privacy.sensitive import profile_set

        found = sum(
            r.removed
            for r in purge_dir(active().captures_dir, profile_set(profile=active()), dry_run=True)
        )
        if found:
            print(
                f"  {ansi.YELLOW}{found} stored capture(s) of it — now hidden from every "
                f"command; remove them with `canair captures purge-sensitive`.{ansi.RESET}"
            )
    return 0


def _int_list(text: str) -> list[int]:
    try:
        return [int(v) for v in text.split(",") if v.strip()]
    except ValueError:
        raise EditError("--also-at takes comma-separated integers") from None


def cmd_set_support_map(args: argparse.Namespace) -> int:
    decl = None
    if not args.clear:
        if args.at is None or args.length is None or args.unit is None:
            raise EditError("give --at, --length and --unit, or --clear")
        raw = {"at": args.at, "length": args.length, "unit": args.unit, "order": args.order}
        if args.also_at:
            raw["also_at"] = _int_list(args.also_at)
        with translate(SupportMapError, EditError):
            decl = MapDecl.from_mapping(raw)

    state = decl.flow() if decl else "cleared"
    return run_edit(
        lambda: set_support_map(args.ecu, args.pid, decl, pids_dir=args.dir),
        f"{args.ecu} {args.pid} support_map -> {state}",
        args=args,
    )


def cmd_set_pid_period(args: argparse.Namespace) -> int:
    value = int_or_clear(args.value)

    state = "cleared (device default 5000)" if value is None else f"{value} ms"
    return run_edit(
        lambda: set_pid_period(args.ecu, args.pid, value, pids_dir=args.dir),
        f"{args.ecu} {args.pid} period -> {state}",
        args=args,
    )


def cmd_set_response_frames(args: argparse.Namespace) -> int:
    value = int_or_clear(args.value)

    state = "cleared" if value is None else str(value)
    return run_edit(
        lambda: set_response_frames(args.ecu, args.pid, value, pids_dir=args.dir),
        f"{args.ecu} {args.pid} response_frames -> {state}",
        args=args,
    )


def cmd_set_pid_notes(args: argparse.Namespace) -> int:
    notes = args.value

    what = words_verb(notes)
    return run_edit(
        lambda: set_pid_notes(args.ecu, args.pid, notes, pids_dir=args.dir),
        f"{args.ecu} {args.pid} notes {what}",
        args=args,
    )


def cmd_set_pid_defaults(args: argparse.Namespace) -> int:
    fields = {
        "unit": args.unit,
        "min": args.min,
        "max": args.max,
        "verified": args.verified,
        "source": args.source,
        "source_links": args.source_link or None,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    clear = args.clear or []

    detail = ", ".join([*fields, *(f"-{c}" for c in clear)])
    return run_edit(
        lambda: set_pid_defaults(args.ecu, args.pid, fields, clear=clear, pids_dir=args.dir),
        f"{args.ecu} {args.pid} defaults: {detail}",
        args=args,
    )


def cmd_hoist_defaults(args: argparse.Namespace) -> int:
    if args.pid and not args.ecu:
        raise EditError("a PID needs its ECU")
    ecus = [args.ecu] if args.ecu else ecu_names(args.dir)
    pids = fields = 0
    for ecu in ecus:
        found: list = []

        def do(ecu=ecu, found=found):
            found.extend(hoist_defaults(ecu, args.pid, dry_run=args.dry_run, pids_dir=args.dir))

        if args.dry_run:
            do()
            fpath = find_ecu_file(ecu, pids_dir=args.dir)
        else:
            fpath = apply_in_scope(args, do, ecu)
        by_pid: dict[str, list] = {}
        for h in found:
            by_pid.setdefault(h.pid, []).append(h)
        for pid, hoisted in by_pid.items():
            names = ", ".join(f"{h.field} ({len(h.params)}/{h.total})" for h in hoisted)
            print(f"  {ecu} {pid}: {names}")
        if found and not args.dry_run:
            echo_edit(f"{ecu} hoisted {len(found)} shared field(s)", fpath)
        pids += len(by_pid)
        fields += len(found)
    verb = "Would hoist" if args.dry_run else "Hoisted"
    print(f"  {verb} {fields} field(s) across {pids} PID(s).")
    return 0
