"""``canair ecu revisions`` / ``canair ecu set-variants`` — an ECU's revisions and presence.

A *revision* is a version of one ECU that differs in protocol or decoding (a KWP2000 VCU
that became UDS, a BMS with another cell count); the first declared is the reference. A
PID or signal is gated to revisions with ``canair pids set-revisions``; a variant says
which revisions its car can carry with ``canair variants set-revisions``. Every edit is
validated and reverted on failure, and a rename rewrites every reference.

Examples:
  canair ecu revisions VCU                         # list VCU's revisions
  canair ecu revisions VCU add kwp --protocol KWP2000
  canair ecu revisions VCU add uds --protocol UDS
  canair ecu revisions VCU set-fingerprint kwp --responds 1A90
  canair ecu revisions VCU set-fingerprint uds --identity part_number='36601-0E1*'
  canair ecu revisions VCU rename uds uds-2018     # rewrites gates, variants, capture tags
  canair ecu revisions VCU rm uds-2018             # refused while anything refers to it
  canair ecu set-variants HEATER 60kwh             # fitted only to the 60 kWh car
  canair ecu set-variants HEATER --clear           # fitted to every variant
"""

from __future__ import annotations

import argparse
import json
import sys

from canlib import ansi
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.edit_echo import echo_edit

_EXAMPLES = __doc__.split("Examples:")[1]


def _run_edit(message: str, action) -> int:
    from canlib.ecus.edit import EcusEditError
    from canlib.profile import require_writable_definitions

    require_writable_definitions()
    try:
        path = action()
    except EcusEditError as e:
        print(f"{ansi.RED}  Error: {e}{ansi.RESET}", file=sys.stderr)
        return 1
    echo_edit(message, path)
    return 0


def cmd_list(args) -> int:
    from canlib.pids import load_raw_pids, revision_resolutions
    from canlib.variants import load_variants
    from canlib.variants.revisions import canonical_ecu, declared, describe_resolution

    ecus = load_raw_pids().get("ecus") or {}
    name = canonical_ecu(args.ecu, ecus)
    if name is None:
        print(f"{ansi.RED}Unknown ECU {args.ecu!r}.{ansi.RESET}", file=sys.stderr)
        return 1
    ecu_def = ecus[name]
    revs = declared(ecu_def)
    block = ecu_def.get("revisions") or {}
    resolution = revision_resolutions().get(name)
    gated = dict.fromkeys(revs, 0)
    for pid_def in (ecu_def.get("pids") or {}).values():
        for node in [pid_def, *((pid_def or {}).get("parameters") or {}).values()]:
            for r in (node or {}).get("revisions") or []:
                gated[str(r).lower()] = gated.get(str(r).lower(), 0) + 1
    allowed_by = {
        r: [vid for vid, v in load_variants().items() if r in (v.revisions.get(name) or revs)]
        for r in revs
    }
    if args.json:
        json.dump(
            {
                "ecu": name,
                "revisions": [
                    {
                        "id": r,
                        "reference": i == 0,
                        **dict(block.get(r) or {}),
                        "gates": gated.get(r, 0),
                        "variants": allowed_by[r],
                    }
                    for i, r in enumerate(revs)
                ],
                "fitted_to": ecu_def.get("variants"),
                "resolved": resolution.revision if resolution else None,
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0
    fitted = ecu_def.get("variants")
    print(
        f"\n  {ansi.c(name, ansi.BOLD)} — fitted to {', '.join(fitted) if fitted else 'every variant'}"
    )
    if not revs:
        print(f"  {ansi.c('no revisions declared (one implicit revision)', ansi.DIM)}\n")
        return 0
    for i, r in enumerate(revs):
        entry = block.get(r) or {}
        proto = (entry.get("identity") or {}).get("id_protocol")
        bits = [
            "reference" if i == 0 else "",
            f"protocol {proto}" if proto else "",
            f"{len(entry.get('fingerprints') or [])} fingerprint(s)",
            f"{gated.get(r, 0)} gated PID/signal(s)",
            f"variants: {', '.join(allowed_by[r]) or 'none'}",
        ]
        print(f"  {ansi.c(r, ansi.CYAN)}  {' · '.join(b for b in bits if b)}")
    if resolution is not None:
        print(f"\n  your car: {describe_resolution(resolution)}")
    print()
    return 0


def cmd_add(args) -> int:
    from canlib.ecus.revisions_edit import add_revision

    return _run_edit(
        f"{args.ecu}: added revision {args.revision}",
        lambda: add_revision(args.ecu, args.revision, protocol=args.protocol),
    )


def cmd_set_fingerprint(args) -> int:
    from canlib.ecus.revisions_edit import set_fingerprint

    identity = {}
    for pair in args.identity or []:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip() or not value.strip():
            print(f"error: --identity expects FIELD=PATTERN, got {pair!r}", file=sys.stderr)
            return 1
        identity[key.strip()] = value.strip()
    return _run_edit(
        f"{args.ecu} {args.revision}: fingerprints updated",
        lambda: set_fingerprint(
            args.ecu,
            args.revision,
            responds=args.responds,
            identity=identity or None,
            clear=args.clear,
        ),
    )


def cmd_rename(args) -> int:
    from canlib.ecus.revisions_edit import rename_revision

    written: list = []

    def action():
        path, others = rename_revision(args.ecu, args.old, args.new)
        written.extend(others)
        return path

    rc = _run_edit(f"{args.ecu}: renamed revision {args.old} → {args.new}", action)
    for path in written:
        print(f"  {ansi.c('rewrote', ansi.DIM)} {path}")
    return rc


def cmd_rm(args) -> int:
    from canlib.ecus.revisions_edit import remove_revision

    return _run_edit(
        f"{args.ecu}: removed revision {args.revision}",
        lambda: remove_revision(args.ecu, args.revision),
    )


def cmd_set_variants(args) -> int:
    from canlib.ecus.revisions_edit import set_ecu_variants

    if args.clear == bool(args.variants):
        print("error: give the variants, or --clear", file=sys.stderr)
        return 1
    ids = [v for part in args.variants for v in part.split(",") if v.strip()]
    what = ", ".join(ids) if ids else "every variant"
    return _run_edit(
        f"{args.ecu}: fitted to {what}",
        lambda: set_ecu_variants(args.ecu, None if args.clear else ids),
    )


def add_parsers(kinds) -> None:
    """Register the ``revisions`` and ``set-variants`` kinds under ``canair ecu``."""
    rev = kinds.add_parser(
        "revisions",
        help="List or edit an ECU's revisions (versions that differ in protocol or decoding)",
        description=__doc__.split("Examples:")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:" + _EXAMPLES,
    )
    rev.add_argument("ecu", metavar="ECU", help="ECU name").completer = _ecu_completer
    rev.add_argument("--json", action="store_true", help="Output the list as JSON")
    sub = rev.add_subparsers(dest="revisions_action", metavar="<action>")

    lst = sub.add_parser("list", help="List the ECU's revisions (default)")
    lst.add_argument("--json", action="store_true", help="Output as JSON")
    lst.set_defaults(func=cmd_list)

    add = sub.add_parser("add", help="Declare a revision (the first declared is the reference)")
    add.add_argument("revision", metavar="ID", help="Revision ID naming what differs (kwp, uds)")
    add.add_argument("--protocol", help="The revision's identity protocol (UDS | KWP2000)")
    add.set_defaults(func=cmd_add)

    fp = sub.add_parser(
        "set-fingerprint",
        help="Add a fingerprint: evidence that recognises the revision",
        description="Add one fingerprint to a revision: a safe identity read only this "
        "revision answers (--responds), or identity fields matching glob patterns "
        "(--identity). Recorded and checked; not yet matched against a car.",
    )
    fp.add_argument("revision", metavar="ID")
    fp.add_argument("--responds", metavar="HEX", help="A safe read only it answers (e.g. 1A90)")
    fp.add_argument(
        "--identity",
        action="append",
        metavar="FIELD=GLOB",
        help="An identity field and the pattern it matches (repeatable, one fingerprint)",
    )
    fp.add_argument("--clear", action="store_true", help="Drop the existing fingerprints first")
    fp.set_defaults(func=cmd_set_fingerprint)

    ren = sub.add_parser(
        "rename",
        help="Rename a revision, rewriting its gates, variants' lists and capture tags",
    )
    ren.add_argument("old", metavar="OLD")
    ren.add_argument("new", metavar="NEW")
    ren.set_defaults(func=cmd_rename)

    rm = sub.add_parser("rm", help="Remove a revision (refused while anything refers to it)")
    rm.add_argument("revision", metavar="ID")
    rm.set_defaults(func=cmd_rm)
    rev.set_defaults(func=cmd_list)

    sv = kinds.add_parser(
        "set-variants",
        help="Say which variants an ECU is fitted to (ECU presence)",
        description="Record that an ECU is fitted only to some of the profile's variants "
        "(its variants: list). Commands for another car then leave it out. --clear: "
        "fitted to every variant (the default).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n  canair ecu set-variants HEATER 60kwh\n"
        "  canair ecu set-variants HEATER --clear\n",
    )
    sv.add_argument("ecu", metavar="ECU", help="ECU name").completer = _ecu_completer
    sv.add_argument("variants", nargs="*", metavar="VARIANT", help="Variant IDs (or comma list)")
    sv.add_argument("--clear", action="store_true", help="Fitted to every variant")
    sv.set_defaults(func=cmd_set_variants)
