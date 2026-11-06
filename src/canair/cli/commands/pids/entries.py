"""``canair pids`` kinds that edit a signal or a whole PID."""

from __future__ import annotations

import argparse

from canair.analysis.signal_range import capture_range
from canair.cli.commands._edit import add_edit_args, edit_scope, run_edit
from canair.core.edit_errors import EditError
from canair.ecus.pid_status import PID_STATUSES
from canair.ecus.signal_category import CATEGORIES, QUANTITY_CATEGORIES
from canair.render import ansi, messages
from canair.yaml_edit.echo import echo_edit
from canair.yaml_edit.pids import (
    add_pid,
    delete_parameter,
    delete_pid,
    rename_parameter,
    rename_pid,
    upsert_parameter,
)

from .common import apply_in_scope


def add_parsers(sub) -> None:
    """Register the kinds of this group under ``canair pids``."""
    up = sub.add_parser("upsert-param", help="Add or update a signal")
    up.add_argument("ecu")
    up.add_argument("pid")
    up.add_argument("name")
    up.add_argument("expression")
    up.add_argument("--unit")
    up.add_argument(
        "--category",
        choices=[*CATEGORIES, ""],
        metavar="CATEGORY",
        help="What the signal measures or represents, only where the unit does not say it: "
        + ", ".join(c for c in CATEGORIES if c not in QUANTITY_CATEGORIES)
        + " (or a quantity, when there is no unit). A two-state one (door, lock…) also "
        "needs --value 0=LABEL --value 1=LABEL. Pass an empty string to remove it",
    )
    up.add_argument(
        "--mqtt-topic",
        dest="mqtt_topic",
        help="Legacy, inert Home Assistant name for the signal (read by no code); "
        "to stream a signal use --send-to",
    )
    up.add_argument(
        "--send-to",
        dest="send_to",
        metavar="TOPIC",
        help="Full MQTT topic the WiCAN publishes this signal to on every poll, instead of "
        "only in the AutoPID batch (e.g. wican/car/stream/battery_power). Must be unique "
        "across shipped signals. Pass an empty string to stop streaming the signal",
    )
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
        help="Enum mapping (repeatable), e.g. --value 33=park --value 40=drive",
    )
    up.add_argument(
        "--bit",
        dest="bits",
        action="append",
        metavar="INDEX=LABEL",
        help="Bitmask mapping (repeatable, 0=LSB), e.g. --bit 5=driver --bit 7=trunk",
    )
    ver = up.add_mutually_exclusive_group()
    ver.add_argument("--verified", dest="verified", action="store_true", default=None)
    ver.add_argument("--unverified", dest="verified", action="store_false")
    ship = up.add_mutually_exclusive_group()
    ship.add_argument("--ship", dest="ship", action="store_true", default=None)
    ship.add_argument("--no-ship", dest="ship", action="store_false", help="Keep off the device")
    up.add_argument(
        "--revision",
        metavar="ID",
        help="The ECU revision this was seen on: an existing signal gets the given fields "
        "under per_revision.ID (the rest stay shared); a new one is created gated to "
        "revisions: [ID], never claimed for every revision",
    )
    add_edit_args(up)
    up.set_defaults(_pids_func=cmd_upsert_param)

    rn = sub.add_parser("rename-param", help="Rename a signal (key only; fields preserved)")
    rn.add_argument("ecu")
    rn.add_argument("pid")
    rn.add_argument("old", help="Current signal name")
    rn.add_argument("new", help="New signal name")
    add_edit_args(rn)
    rn.set_defaults(_pids_func=cmd_rename_param)

    rm = sub.add_parser("rm-param", help="Remove a signal")
    rm.add_argument("ecu")
    rm.add_argument("pid")
    rm.add_argument("name")
    add_edit_args(rm)
    rm.set_defaults(_pids_func=cmd_rm_param)

    rnp = sub.add_parser("rename-pid", help="Rename a PID key (e.g. B002 -> 22B002)")
    rnp.add_argument("ecu")
    rnp.add_argument("old", help="Current PID code")
    rnp.add_argument("new", help="New PID code (hex, e.g. 22B002)")
    add_edit_args(rnp)
    rnp.set_defaults(_pids_func=cmd_rename_pid)

    rmp = sub.add_parser("rm-pid", help="Remove a whole PID (header, status, parameters)")
    rmp.add_argument("ecu")
    rmp.add_argument("pid")
    add_edit_args(rmp)
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
    add_edit_args(adp)
    adp.set_defaults(_pids_func=cmd_add_pid)


def _parse_pairs(raw: list[str] | None, kind: str) -> dict | None:
    """Parse repeatable ``KEY=LABEL`` CLI args into an int-keyed dict."""
    if not raw:
        return None
    out: dict[int, str] = {}
    for item in raw:
        if "=" not in item:
            raise EditError(f"--{kind} expects KEY=LABEL, got {item!r}")
        k, _, v = item.partition("=")
        try:
            out[int(k.strip(), 0)] = v.strip()
        except ValueError:
            raise EditError(f"--{kind} key must be an integer, got {k!r}") from None
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
        found = capture_range(ecu, pid, name, param)
        if not found.captures:
            print(
                f"    {ansi.DIM}(no captures for {ecu} {pid} yet — expression unverified){ansi.RESET}"
            )
            return

        displays, values, err = found.displays, found.values, found.error
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
            print(f"    {ansi.RED}captures: error — {err}{ansi.RESET}")
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

    revision = getattr(args, "revision", None)
    if revision and _signal_exists(args.ecu, args.pid, args.name, args.dir):
        return _upsert_revision_override(args, ptype, values, bits)

    def do():
        upsert_parameter(
            args.ecu,
            args.pid,
            args.name,
            args.expression,
            unit=args.unit,
            category=args.category,
            mqtt_topic=args.mqtt_topic,
            send_to=args.send_to,
            min=args.min,
            max=args.max,
            source=args.source,
            source_links=args.source_link or None,
            verified=args.verified,
            notes=args.notes,
            ship=args.ship,
            display=args.display,
            type=ptype,
            values=values,
            bits=bits,
            pids_dir=args.dir,
        )

    fpath = apply_in_scope(args, do)
    if revision:
        # A new signal seen on one revision exists only there until shown otherwise.
        _gate_new_signal(args, revision)
    echo_edit(f"{args.ecu} {args.pid} {args.name}", fpath)

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


def _signal_exists(ecu: str, pid: str, name: str, pids_dir) -> bool:
    from canair.ecus.pids import load_pids_dir, load_raw_pids

    data = load_pids_dir(pids_dir) if pids_dir else load_raw_pids()
    ecu_def = next(
        (d for k, d in (data.get("ecus") or {}).items() if str(k).upper() == ecu.upper()), None
    )
    pid_def = next(
        (
            d
            for k, d in ((ecu_def or {}).get("pids") or {}).items()
            if str(k).upper() == pid.upper()
        ),
        None,
    )
    return name in ((pid_def or {}).get("parameters") or {})


def _gate_new_signal(args: argparse.Namespace, revision: str) -> None:
    from canair.yaml_edit.revisions import set_gate

    set_gate(args.ecu, args.pid, args.name, [revision])


def _upsert_revision_override(args: argparse.Namespace, ptype, values, bits) -> int:
    """``upsert-param --revision R`` on an existing signal: write ``per_revision.R``."""
    from canair.yaml_edit.revisions import set_override

    if args.dir is not None:
        raise EditError("--revision does not take --dir")
    fields: dict = {"expression": args.expression}
    for key, value in (
        ("unit", args.unit),
        ("min", args.min),
        ("max", args.max),
        ("source", args.source),
        ("source_links", args.source_link or None),
        ("verified", args.verified),
        ("notes", args.notes),
        ("type", ptype),
        ("values", values),
        ("bits", bits),
    ):
        if value is not None:
            fields[key] = value
    unsupported = [
        flag
        for flag, value in (
            ("--category", args.category),
            ("--mqtt-topic", args.mqtt_topic),
            ("--send-to", args.send_to),
            ("--display", args.display),
            ("--ship/--no-ship", args.ship),
        )
        if value is not None
    ]
    if unsupported:
        raise EditError(f"{', '.join(unsupported)} cannot differ by revision")
    with edit_scope(args):
        fpath = set_override(args.ecu, args.pid, args.name, args.revision, fields)
    echo_edit(f"{args.ecu} {args.pid} {args.name} (revision {args.revision})", fpath)
    return 0


def _warn_state_predicates(ecu: str, param: str, consequence: str, hint: str) -> None:
    """Warn when a vehicle-state ``when:`` predicate reads the signal just edited.

    A predicate references a signal by name, so renaming or removing that signal
    silently stops the state from ever matching — a missing signal is
    indistinguishable from a not-polled one at evaluation time (see
    :mod:`canair.states.refs`). ``canair validate states`` is the gate; this is the
    nudge at the moment the breakage is introduced.
    """
    try:
        from canair.states.refs import states_referencing

        states = states_referencing(ecu, param)
    except Exception:
        return  # never let an advisory check fail a successful write
    if not states:
        return
    messages.warning(
        f"vehicle_states.yaml predicate(s) {', '.join(states)} read {ecu.upper()}.{param} "
        f"— {consequence}"
    )
    messages.hint(hint.removeprefix("fix: "))


def cmd_rename_param(args: argparse.Namespace) -> int:
    def do():
        rename_parameter(args.ecu, args.pid, args.old, args.new, pids_dir=args.dir)

    fpath = apply_in_scope(args, do)
    echo_edit(f"{args.ecu} {args.pid} {args.old} → {args.new}", fpath)
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

    fpath = apply_in_scope(args, do)
    echo_edit(f"removed {args.ecu} {args.pid} {args.name}", fpath)
    _warn_state_predicates(
        args.ecu,
        args.name,
        "the removal leaves them permanently unmatched",
        "fix: canair states set-predicate NAME EXPR (or clear it: canair states "
        "set-predicate NAME)",
    )
    return 0


def cmd_rename_pid(args: argparse.Namespace) -> int:
    return run_edit(
        lambda: rename_pid(args.ecu, args.old, args.new, pids_dir=args.dir),
        f"{args.ecu} {args.old} → {args.new}",
        args=args,
    )


def cmd_rm_pid(args: argparse.Namespace) -> int:
    return run_edit(
        lambda: delete_pid(args.ecu, args.pid, pids_dir=args.dir),
        f"removed {args.ecu} {args.pid}",
        args=args,
    )


def cmd_add_pid(args: argparse.Namespace) -> int:
    return run_edit(
        lambda: add_pid(
            args.ecu,
            args.pid,
            status=args.status,
            vehicle_states=args.prereq or None,
            period=args.period,
            notes=args.notes,
            pids_dir=args.dir,
        ),
        f"{args.ecu} {args.pid} [{args.status}]",
        args=args,
    )
