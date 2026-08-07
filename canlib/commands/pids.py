"""``canair pids`` — safely add/update ecus/ parameters and research entries.

A thin, non-interactive wrapper over canlib.pids_edit for the reverse-
engineering workflow. Every edit is:

  1. applied surgically (comments/formatting preserved, YAML re-parsed), then
  2. schema-checked in-process (canair validate pids) — reverted if it fails.

Subcommands:
  upsert-param ECU PID NAME EXPR   Add/update a parameter (expression required)
  rename-param ECU PID OLD NEW     Rename a parameter key (fields preserved)
  rm-param     ECU PID NAME        Remove a parameter
  rename-pid   ECU OLD NEW         Rename a PID key (e.g. B002 -> 22B002)
  rm-pid       ECU PID             Remove a whole PID (header, status, parameters)
  add-research ECU ...             Append a research: entry
  set-status  ECU TARGET STATUS    Update a research item's status
  set-pid-status ECU PID STATUS    Set a PID's lifecycle (active|draft|static|ignored)
  set-pid-notes ECU PID [TEXT]     Set (or clear) a PID's free-text notes
  set-identity ECU FIELD VALUE     Set a curated identity field (e.g. notes)
  rm-identity  ECU FIELD           Remove an identity field (e.g. a redundant duplicate)
  set-can-bus  ECU CODE [CODE ...] Set the physical CAN bus segment(s) (see can_buses.yaml)
  set-iocontrol-ranges ECU RANGE ... Set the 0x2F scan DID ranges (e.g. B000-BFFF)
  set-wake     ECU --method ...     Set how to rouse a fast-sleeping ECU before reads
  set-addressing ECU ...           Set CAN addressing (mode / extension bytes / fc_id / rx_id)

Examples:
  # Record a decoded parameter
  canair pids upsert-param MCU 2102 MCU_MOTOR_TORQUE "[S12:S13]/100" \\
      --unit Nm --min -300 --max 300 --unverified --source "Soul VMCU CSV" \\
      --notes "signed 16-bit BE at B12:B13"

  # Track a new investigation, then close it out
  canair pids add-research MCU --type decode --target 2103 \\
      --status captured --priority P2 --prereq charging --notes "27 bytes, zeros"
  canair pids set-status MCU 2103 done --type decode

  # Set an ECU's curated identity notes (comment/format-preserving)
  canair pids set-identity BMS notes "Battery Management System — see sessions:."
"""

from __future__ import annotations

import argparse
from pathlib import Path

from canlib import ansi
from canlib.commands._hexarg import HexArgError, parse_hex_arg
from canlib.pids import PID_STATUSES
from canlib.pids_edit import (
    PidsEditError,
    add_pid,
    add_research_entry,
    delete_parameter,
    delete_pid,
    find_ecu_file,
    remove_identity_field,
    rename_parameter,
    rename_pid,
    set_can_bus,
    set_identity_field,
    set_iocontrol_scan_ranges,
    set_pid_notes,
    set_pid_status,
    set_pid_variable_length,
    set_research_status,
    set_wake,
    upsert_parameter,
)

NAME = "pids"


def _schema_validate(fpath: Path) -> tuple[bool, str]:
    """Validate a single pids file in-process. Returns (ok, output)."""
    from canlib.commands.validate import validate_pids_file

    return validate_pids_file(fpath)


def _guarded(ecu: str, pids_dir: Path | None, do_edit, *, validate: bool):
    """Snapshot -> edit -> schema-validate -> commit or roll back."""
    fpath = find_ecu_file(ecu, pids_dir=pids_dir)
    snapshot = fpath.read_text()
    do_edit()
    if validate:
        ok, out = _schema_validate(fpath)
        if not ok:
            fpath.write_text(snapshot)
            print(out)
            raise SystemExit(
                f"{ansi.RED}  Schema validation failed — reverted {fpath.name}{ansi.RESET}"
            )
    return fpath


def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--dir", type=Path, default=None, help="ecus/ directory (default: active profile)"
    )
    sp.add_argument(
        "--no-validate", action="store_true", help="Skip the post-edit schema validation gate"
    )


def _parse_pairs(raw: list[str] | None, kind: str) -> dict | None:
    """Parse repeatable ``KEY=LABEL`` CLI args into an int-keyed dict."""
    if not raw:
        return None
    out: dict[int, str] = {}
    for item in raw:
        if "=" not in item:
            raise SystemExit(
                f"{ansi.RED}  Error: --{kind} expects KEY=LABEL, got {item!r}{ansi.RESET}"
            )
        k, _, v = item.partition("=")
        try:
            out[int(k.strip(), 0)] = v.strip()
        except ValueError:
            raise SystemExit(
                f"{ansi.RED}  Error: --{kind} key must be an integer, got {k!r}{ansi.RESET}"
            ) from None
    return out


def _echo_capture_range(ecu: str, pid: str, name: str, param: dict) -> None:
    """Print the just-written param's decoded value range across existing captures.

    A passive sanity-check echoed after a successful ``upsert-param``: a wrong
    byte offset (e.g. a WiCAN Bnn landing on an ISO-TP PCI framing byte) shows up
    immediately as a nonsensical ``constant`` or an error, instead of being
    silently persisted. Never fails the write — any problem loading/decoding
    captures is swallowed (the edit already succeeded and was validated).
    """
    try:
        from canlib.byteindex import payload_to_wican_bytes
        from canlib.capture_store import load_pid_captures
        from canlib.decoding import decode_payload

        caps = load_pid_captures(ecu, pid)
        if not caps:
            print(
                f"    {ansi.DIM}(no captures for {ecu} {pid} yet — expression unverified){ansi.RESET}"
            )
            return

        values: list[float] = []
        displays: list[str] = []
        err: str | None = None
        for cap in caps:
            try:
                wb = payload_to_wican_bytes(cap["payload"])
            except Exception:
                continue
            decoded = decode_payload(wb, {name: param}).get(name)
            if not decoded:
                continue
            if decoded.get("error"):
                err = decoded["error"]
                continue
            if decoded.get("display") is not None:
                if decoded["display"] not in displays:
                    displays.append(decoded["display"])
            elif decoded.get("value") is not None:
                values.append(decoded["value"])

        unit = param.get("unit") or ""
        if displays:
            shown = "  ".join(displays[:6]) + ("  …" if len(displays) > 6 else "")
            tag = "(constant)" if len(displays) == 1 else f"({len(displays)} values)"
            print(f"    {ansi.DIM}captures:{ansi.RESET} {shown}  {ansi.DIM}{tag}{ansi.RESET}")
        elif values:
            mn, mx = min(values), max(values)
            if mn == mx:
                print(
                    f"    {ansi.YELLOW}captures: constant {mn:g}{unit}{ansi.RESET}"
                    f"  {ansi.DIM}(check the offset if you expected variation){ansi.RESET}"
                )
            else:
                print(f"    {ansi.DIM}captures:{ansi.RESET} {mn:g}{unit} — {mx:g}{unit}")
        elif err:
            print(f"    {ansi.RED}captures: ERROR — {err}{ansi.RESET}")
        else:
            print(f"    {ansi.DIM}(captures present but expression yielded no value){ansi.RESET}")
    except Exception:
        # Never let a sanity-check echo break a successful write.
        pass


def cmd_upsert_param(args: argparse.Namespace) -> int:
    values = _parse_pairs(args.values, "value")
    bits = _parse_pairs(args.bits, "bit")
    ptype = args.ptype
    # Infer type from the map if only --value/--bit was given.
    if ptype is None:
        if values is not None:
            ptype = "enum"
        elif bits is not None:
            ptype = "bitmask"

    def do():
        upsert_parameter(
            args.ecu,
            args.pid,
            args.name,
            args.expression,
            unit=args.unit,
            ha_class=args.ha_class,
            mqtt_topic=args.mqtt_topic,
            min=args.min,
            max=args.max,
            source=args.source,
            source_links=args.source_link or None,
            verified=args.verified,
            notes=args.notes,
            enabled=args.enabled,
            display=args.display,
            type=ptype,
            values=values,
            bits=bits,
            pids_dir=args.dir,
        )

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} {args.pid} {args.name}{ansi.RESET}  {ansi.DIM}({fpath.name}){ansi.RESET}"
    )

    # Passive sanity-check: decode the new expression against existing captures so
    # a wrong byte offset surfaces at write time (a PCI byte reads constant, etc.).
    param = {
        "expression": args.expression,
        "unit": args.unit,
        "min": args.min,
        "max": args.max,
        "type": ptype or "numeric",
        "values": values,
        "bits": bits,
    }
    _echo_capture_range(args.ecu, args.pid, args.name, param)
    return 0


def _warn_state_predicates(ecu: str, param: str, consequence: str, hint: str) -> None:
    """Warn when a vehicle-state ``when:`` predicate reads the signal just edited.

    A predicate references a signal by name, so renaming or removing that signal
    silently stops the state from ever matching — a missing signal is
    indistinguishable from a not-polled one at evaluation time (see
    :mod:`canlib.state_refs`). ``canair validate states`` is the gate; this is the
    nudge at the moment the breakage is introduced.
    """
    try:
        from canlib.state_refs import states_referencing

        states = states_referencing(ecu, param)
    except Exception:
        return  # never let an advisory check fail a successful write
    if not states:
        return
    print(
        f"  {ansi.YELLOW}warn:{ansi.RESET} vehicle_states.yaml predicate(s) "
        f"{', '.join(states)} read {ecu.upper()}.{param} — {consequence}"
    )
    print(f"    {ansi.DIM}{hint}{ansi.RESET}")


def cmd_rename_param(args: argparse.Namespace) -> int:
    def do():
        rename_parameter(args.ecu, args.pid, args.old, args.new, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} {args.pid} {args.old} → {args.new}{ansi.RESET}  "
        f"{ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    _warn_state_predicates(
        args.ecu,
        args.old,
        "the rename leaves them permanently unmatched",
        f"fix: canair states set-predicate NAME EXPR (with {args.old} → {args.new})",
    )
    return 0


def cmd_rm_param(args: argparse.Namespace) -> int:
    def do():
        delete_parameter(args.ecu, args.pid, args.name, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ removed {args.ecu} {args.pid} {args.name}{ansi.RESET}  "
        f"{ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    _warn_state_predicates(
        args.ecu,
        args.name,
        "the removal leaves them permanently unmatched",
        "fix: canair states set-predicate NAME EXPR (or clear it: canair states "
        "set-predicate NAME)",
    )
    return 0


def cmd_rename_pid(args: argparse.Namespace) -> int:
    def do():
        rename_pid(args.ecu, args.old, args.new, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} {args.old} → {args.new}{ansi.RESET}  {ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_rm_pid(args: argparse.Namespace) -> int:
    def do():
        delete_pid(args.ecu, args.pid, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ removed {args.ecu} {args.pid}{ansi.RESET}  {ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_add_pid(args: argparse.Namespace) -> int:
    def do():
        add_pid(
            args.ecu,
            args.pid,
            status=args.status,
            vehicle_states=args.prereq or None,
            period=args.period,
            notes=args.notes,
            pids_dir=args.dir,
        )

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} {args.pid} [{args.status}]{ansi.RESET}  {ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_add_research(args: argparse.Namespace) -> int:
    def do():
        add_research_entry(
            args.ecu,
            type=args.type,
            target=args.target,
            status=args.status,
            priority=args.priority,
            vehicle_states=args.prereq or None,
            created=args.created,
            updated=args.updated,
            date=args.date,
            result=args.result,
            notes=args.notes,
            sources=args.source or None,
            what_to_test=args.what_to_test or None,
            capture_protocol=args.capture_protocol,
            pids_dir=args.dir,
        )

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ research {args.ecu} {args.type} {args.target} "
        f"[{args.status}]{ansi.RESET}  {ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_set_status(args: argparse.Namespace) -> int:
    def do():
        set_research_status(args.ecu, args.target, args.status, type=args.type, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} research {args.target} -> {args.status}{ansi.RESET}  "
        f"{ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_set_pid_status(args: argparse.Namespace) -> int:
    def do():
        set_pid_status(args.ecu, args.pid, args.status, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} {args.pid} status -> {args.status}{ansi.RESET}  "
        f"{ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_set_pid_variable_length(args: argparse.Namespace) -> int:
    value = args.value == "true"

    def do():
        set_pid_variable_length(args.ecu, args.pid, value, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    state = "true" if value else "false (cleared)"
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} {args.pid} variable_length -> {state}{ansi.RESET}  "
        f"{ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_set_pid_notes(args: argparse.Namespace) -> int:
    notes = args.value

    def do():
        set_pid_notes(args.ecu, args.pid, notes, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    what = "cleared" if not (notes or "").strip() else f"set ({len((notes or '').split())} words)"
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} {args.pid} notes {what}{ansi.RESET}  {ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_set_identity(args: argparse.Namespace) -> int:
    def do():
        set_identity_field(args.ecu, args.field, args.value, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} identity.{args.field} updated{ansi.RESET}  "
        f"{ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_rm_identity(args: argparse.Namespace) -> int:
    def do():
        remove_identity_field(args.ecu, args.field, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} identity.{args.field} removed{ansi.RESET}  "
        f"{ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_set_can_bus(args: argparse.Namespace) -> int:
    from canlib.can_buses import allowed_can_buses
    from canlib.profile import active, profile_for_path

    # Resolve the vocabulary from the profile owning the target ecus/ dir
    # (or the active profile). Enforced only when the profile declares one.
    if args.dir:
        allowed = allowed_can_buses(profile_for_path(args.dir))
    else:
        allowed = allowed_can_buses(active())
    if allowed:
        bad = [c for c in args.codes if c not in allowed]
        if bad:
            raise SystemExit(
                f"{ansi.RED}  Error: unknown CAN bus code(s) {bad} — "
                f"declared in can_buses.yaml: {sorted(allowed)}{ansi.RESET}"
            )

    def do():
        set_can_bus(args.ecu, args.codes, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} can_bus -> [{', '.join(args.codes)}]{ansi.RESET}  "
        f"{ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_set_iocontrol_ranges(args: argparse.Namespace) -> int:
    def do():
        set_iocontrol_scan_ranges(args.ecu, args.ranges, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} iocontrol_scan_ranges -> [{', '.join(args.ranges)}]{ansi.RESET}  "
        f"{ansi.DIM}({fpath.name}){ansi.RESET}"
    )
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

    def do():
        set_wake(args.ecu, fields, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    detail = ", ".join(f"{k}={v}" for k, v in fields.items() if k != "notes")
    print(
        f"{ansi.GREEN}  ✓ {args.ecu} wake -> {{{detail}}}{ansi.RESET}  {ansi.DIM}({fpath.name}){ansi.RESET}"
    )
    return 0


def cmd_set_addressing(args: argparse.Namespace) -> int:
    from canlib import yaml_io
    from canlib.ecus_edit import EcusEditError, set_addressing, tx_key
    from canlib.pids_edit import find_ecu_file

    if not any(
        getattr(args, k) is not None
        for k in ("mode", "target_address", "source_address", "fc_id", "rx_id")
    ):
        raise SystemExit(
            f"{ansi.RED}  Error: nothing to set — pass at least one of --mode/"
            f"--target-address/--source-address/--fc-id/--rx-id{ansi.RESET}"
        )

    fpath = find_ecu_file(args.ecu, pids_dir=args.dir)
    doc = yaml_io.safe_load(fpath.read_text()) or {}
    tx_id = next(
        (int(d["tx_id"]) for d in doc.values() if isinstance(d, dict) and "tx_id" in d),
        None,
    )
    if tx_id is None:
        raise SystemExit(f"{ansi.RED}  Error: no tx_id found in {fpath.name}{ansi.RESET}")

    try:
        changed = set_addressing(
            tx_id,
            mode=args.mode,
            target_address=parse_hex_arg(args.target_address, "target-address"),
            source_address=parse_hex_arg(args.source_address, "source-address"),
            fc_id=parse_hex_arg(args.fc_id, "fc-id"),
            rx_id=parse_hex_arg(args.rx_id, "rx-id"),
            ecus_dir=args.dir,
        )
    except HexArgError as e:
        raise SystemExit(str(e)) from None
    except EcusEditError as e:
        raise SystemExit(f"{ansi.RED}  Error: {e}{ansi.RESET}") from None

    disp = tx_key(tx_id)
    if changed:
        print(
            f"{ansi.GREEN}  ✓ {args.ecu} addressing updated ({disp}){ansi.RESET}  {ansi.DIM}({fpath.name}){ansi.RESET}"
        )
    else:
        print(
            f"{ansi.DIM}  {args.ecu} ({disp}) addressing already as requested; nothing to change.{ansi.RESET}"
        )
    return 0


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Author diagnostic PID params/research in ecus/ (domain A; see `signals` for domain B)",
        description="Safely edit ecus/ parameters and research entries (domain A — "
        "diagnostic UDS PIDs, freeform WiCAN expressions). The broadcast-frame "
        "(domain B) authoring counterpart is `canair signals` (linear signals/ maps).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    sub = parser.add_subparsers(dest="pids_command", required=True)

    up = sub.add_parser("upsert-param", help="Add or update a parameter")
    up.add_argument("ecu")
    up.add_argument("pid")
    up.add_argument("name")
    up.add_argument("expression")
    up.add_argument("--unit")
    up.add_argument("--ha-class", dest="ha_class")
    up.add_argument("--mqtt-topic", dest="mqtt_topic")
    up.add_argument("--min")
    up.add_argument("--max")
    up.add_argument("--source")
    up.add_argument("--source-link", action="append", metavar="URL")
    up.add_argument("--display")
    up.add_argument("--notes")
    up.add_argument(
        "--type",
        dest="ptype",
        choices=["numeric", "enum", "bitmask", "ascii", "date", "bcd"],
        help="Typed decoding: enum/bitmask/ascii/date/bcd (default numeric). "
        "See --value / --bit for the enum/bitmask maps.",
    )
    up.add_argument(
        "--value",
        dest="values",
        action="append",
        metavar="RAW=LABEL",
        help="Enum mapping (repeatable), e.g. --value 40=fan1 --value 45=fanMAX",
    )
    up.add_argument(
        "--bit",
        dest="bits",
        action="append",
        metavar="INDEX=LABEL",
        help="Bitmask mapping (repeatable, 0=LSB), e.g. --bit 0=mon --bit 5=sat",
    )
    ver = up.add_mutually_exclusive_group()
    ver.add_argument("--verified", dest="verified", action="store_true", default=None)
    ver.add_argument("--unverified", dest="verified", action="store_false")
    en = up.add_mutually_exclusive_group()
    en.add_argument("--enabled", dest="enabled", action="store_true", default=None)
    en.add_argument("--disabled", dest="enabled", action="store_false")
    _add_common(up)
    up.set_defaults(_pids_func=cmd_upsert_param)

    rn = sub.add_parser("rename-param", help="Rename a parameter (key only; fields preserved)")
    rn.add_argument("ecu")
    rn.add_argument("pid")
    rn.add_argument("old", help="Current parameter name")
    rn.add_argument("new", help="New parameter name")
    _add_common(rn)
    rn.set_defaults(_pids_func=cmd_rename_param)

    rm = sub.add_parser("rm-param", help="Remove a parameter")
    rm.add_argument("ecu")
    rm.add_argument("pid")
    rm.add_argument("name")
    _add_common(rm)
    rm.set_defaults(_pids_func=cmd_rm_param)

    rnp = sub.add_parser("rename-pid", help="Rename a PID key (e.g. B002 -> 22B002)")
    rnp.add_argument("ecu")
    rnp.add_argument("old", help="Current PID code")
    rnp.add_argument("new", help="New PID code (hex, e.g. 22B002)")
    _add_common(rnp)
    rnp.set_defaults(_pids_func=cmd_rename_pid)

    rmp = sub.add_parser("rm-pid", help="Remove a whole PID (header, status, parameters)")
    rmp.add_argument("ecu")
    rmp.add_argument("pid")
    _add_common(rmp)
    rmp.set_defaults(_pids_func=cmd_rm_pid)

    adp = sub.add_parser(
        "add-pid", help="Create a new parameter-less PID (discovery/identity placeholder)"
    )
    adp.add_argument("ecu")
    adp.add_argument("pid", help="PID code, hex (e.g. 21F2)")
    adp.add_argument(
        "--status",
        choices=list(PID_STATUSES),
        default="draft",
        help="PID lifecycle (default: draft — swept/queryable but not shipped)",
    )
    adp.add_argument(
        "--prereq",
        "--vehicle-states",
        dest="prereq",
        action="append",
        type=str.upper,
        help="Power state(s) in which this PID responds (repeatable). Validated "
        "against the profile's vehicle-state vocabulary at write time.",
    )
    adp.add_argument("--period", type=int, help="Polling interval in ms")
    adp.add_argument("--notes", help="Freeform notes for the PID")
    _add_common(adp)
    adp.set_defaults(_pids_func=cmd_add_pid)

    ar = sub.add_parser("add-research", help="Append a research: entry")
    ar.add_argument("ecu")
    ar.add_argument("--type", required=True, choices=["scan", "decode", "verify", "iocontrol_scan"])
    ar.add_argument("--target", required=True)
    ar.add_argument("--status", required=True, choices=["pending", "captured", "nrc", "done"])
    ar.add_argument("--priority", choices=["P1", "P2", "P3"])
    ar.add_argument(
        "--prereq",
        "--vehicle-states",
        dest="prereq",
        action="append",
        type=str.upper,
        help="Power state(s) prerequisite for this research (repeatable). "
        "Validated against the profile's vehicle-state vocabulary at write time.",
    )
    ar.add_argument("--date")
    ar.add_argument(
        "--created", metavar="YYYY-MM-DD", help="Override auto creation date (default: today)"
    )
    ar.add_argument(
        "--updated", metavar="YYYY-MM-DD", help="Override auto updated date (default: today)"
    )
    ar.add_argument("--result")
    ar.add_argument("--notes")
    ar.add_argument("--source", action="append", metavar="SRC")
    ar.add_argument("--what-to-test", action="append", metavar="ITEM")
    ar.add_argument("--capture-protocol", metavar="TEXT")
    _add_common(ar)
    ar.set_defaults(_pids_func=cmd_add_research)

    ss = sub.add_parser("set-status", help="Update a research item's status")
    ss.add_argument("ecu")
    ss.add_argument("target")
    ss.add_argument("status", choices=["pending", "captured", "nrc", "done"])
    ss.add_argument(
        "--type",
        choices=["scan", "decode", "verify", "iocontrol_scan"],
        help="Disambiguate when multiple items share the target",
    )
    _add_common(ss)
    ss.set_defaults(_pids_func=cmd_set_status)

    sps = sub.add_parser("set-pid-status", help="Set a PID's lifecycle status")
    sps.add_argument("ecu")
    sps.add_argument("pid")
    sps.add_argument("status", choices=list(PID_STATUSES))
    _add_common(sps)
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
        help="true = variable-length (a short payload is not truncation); "
        "false = clear the flag (fixed-length, the default)",
    )
    _add_common(spv)
    spv.set_defaults(_pids_func=cmd_set_pid_variable_length)

    spn = sub.add_parser(
        "set-pid-notes",
        help="Set (or clear) a PID's free-text notes",
        description=(
            "Set the PID-level notes: — the record of what the page is and what is "
            "known about it. Because that record goes stale as decoding progresses, "
            "correcting it needs a validated editor rather than a hand-edit.\n\n"
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
    _add_common(spn)
    spn.set_defaults(_pids_func=cmd_set_pid_notes)

    si = sub.add_parser("set-identity", help="Set a curated identity field (e.g. notes)")
    si.add_argument("ecu")
    si.add_argument("field", help="Identity field name, e.g. notes or description")
    si.add_argument("value", help="New value (notes are stored as a folded block scalar)")
    _add_common(si)
    si.set_defaults(_pids_func=cmd_set_identity)

    ri = sub.add_parser("rm-identity", help="Remove an identity field from an ECU")
    ri.add_argument("ecu")
    ri.add_argument("field", help="Identity field name to drop, e.g. software")
    _add_common(ri)
    ri.set_defaults(_pids_func=cmd_rm_identity)

    scb = sub.add_parser("set-can-bus", help="Set the physical CAN bus segment(s) the ECU sits on")
    scb.add_argument("ecu")
    scb.add_argument(
        "codes",
        nargs="+",
        metavar="CODE",
        help="One or more bus codes from the profile's can_buses.yaml "
        "(Hyundai: B-CAN/P-CAN/C-CAN/MM-CAN/H-CAN/ALL); some ECUs span two, e.g. H-CAN P-CAN",
    )
    _add_common(scb)
    scb.set_defaults(_pids_func=cmd_set_can_bus)

    sir = sub.add_parser(
        "set-iocontrol-ranges",
        help="Set the IOControl (0x2F) scan DID ranges swept on this ECU",
        description="Set the ECU's iocontrol_scan_ranges: list — the "
        "'START-END' hex DID ranges the `canair scan iocontrol` sweep covers. "
        "When unset, ranges are derived from the ECU's known 2F/22 DID keys, "
        "else the full DID space (0000-FFFF). This replaces the old hardcoded "
        "HK body-controller zones with a per-ECU, profile-declared range.",
    )
    sir.add_argument("ecu")
    sir.add_argument(
        "ranges",
        nargs="+",
        metavar="RANGE",
        help="One or more 'START-END' hex DID ranges (e.g. B000-BFFF C000-C0FF)",
    )
    _add_common(sir)
    sir.set_defaults(_pids_func=cmd_set_iocontrol_ranges)

    sw = sub.add_parser(
        "set-wake",
        help="Set how to rouse a fast-sleeping ECU before reads (wake ritual)",
        description="Declare a per-ECU wake ritual (canlib.wake). Some modules "
        "(e.g. a Smart Key Module) power their CAN transceiver only briefly and "
        "sleep again within a second or two, so a single 10 01 wake races the "
        "sleep timer. `rapid_read` fires a cheap prime PID back-to-back to hold "
        "the transceiver awake, then opens a session — honoured by "
        "`session <ECU> --wake` on both transports.",
    )
    sw.add_argument("ecu")
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
    _add_common(sw)
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
    sad.add_argument("ecu")
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
    _add_common(sad)
    sad.set_defaults(_pids_func=cmd_set_addressing)

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    try:
        return args._pids_func(args)
    except PidsEditError as e:
        raise SystemExit(f"{ansi.RED}  Error: {e}{ansi.RESET}") from None


if __name__ == "__main__":
    import sys

    from canlib.cli import main

    sys.exit(main(["pids", *sys.argv[1:]]))
