"""``canair variants`` — list and edit the variants of the profile's car.

A *variant* is a version of the car the owner can name — a battery size, a facelift, a
market with different diagnostics — declared in ``profiles/<name>/variants.yaml`` with a
stable ID and metadata. A bare ``canair variants`` lists them; the edit subcommands
modify the file surgically (comment-preserving, checked, reverted on failure).

Metadata values are typed per field: short lists (``markets``, ``trims``) are
comma-separated, ``sources`` takes one source per ``sources=…`` (repeat it for more),
battery fields are set one at a time as ``battery.<field>``, and
``production``/``model_years`` take a ``from,to`` pair, and ``profile.yaml`` overrides are
set one at a time as ``profile_overrides.<setting>``. Setting a field replaces it.

Examples:
  canair variants                                   # list the variants
  canair variants show 38kwh --json
  canair variants add 38kwh --name "Ioniq Electric 38 kWh (facelift)" \\
      production=2019-06,2022 battery.usable_kwh=38.3 status=seed
  canair variants set-meta 28kwh model_code=AEEV markets=EU,US,KR
  canair variants unset-meta 28kwh trims
  canair variants set-meta 38kwh profile_overrides.car_model="Ioniq Electric 38 kWh"
  canair variants set-revisions 38kwh BMS uds        # the 38 kWh car's BMS is the UDS one
  canair variants rename 28kwh 28kwh-eu
  canair variants rm 38kwh
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from canlib import ansi
from canlib.edit_echo import echo_edit

NAME = "variants"


def _load(prof):
    from canlib.variants import VariantsError, load_variants

    try:
        return load_variants(prof)
    except VariantsError as e:
        raise SystemExit(f"{ansi.c('Invalid variants.yaml:', ansi.RED)} {e}") from None


def _record(variant) -> dict:
    return {k: v for k, v in dataclasses.asdict(variant).items() if v not in (None, (), [])}


def cmd_list(args) -> int:
    from canlib.profile import active
    from canlib.variants import describe

    prof = active()
    variants = _load(prof)
    if args.json:
        json.dump(
            {
                "variants": [_record(v) for v in variants.values()],
                "source": str(prof.variants_file),
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0
    if not variants:
        print(
            f"\n  No variants declared for profile {ansi.c(prof.name, ansi.CYAN)} "
            f"{ansi.c('(no variants.yaml)', ansi.DIM)}.\n"
            f"  Add one with {ansi.c('canair variants add ID --name NAME', ansi.CYAN)}.\n"
        )
        return 0
    print(
        f"\n  {ansi.c('Variants', ansi.BOLD)} — {len(variants)} in {ansi.c(prof.name, ansi.CYAN)}\n"
    )
    width = max(len(v) for v in variants)
    for vid, variant in variants.items():
        print(f"  {ansi.c(vid.ljust(width), ansi.CYAN)}  {describe(variant)}")
    print(f"\n  {ansi.c(f'source: {prof.variants_file}', ansi.DIM)}\n")
    return 0


def cmd_show(args) -> int:
    from canlib.profile import active
    from canlib.variants import normalize_variant_id

    prof = active()
    variants = _load(prof)
    vid = normalize_variant_id(args.id)
    if vid not in variants:
        known = ", ".join(variants) or "none"
        print(f"error: no variant {vid!r} in {prof.name} (declared: {known})", file=sys.stderr)
        return 1
    record = _record(variants[vid])
    if args.json:
        json.dump(record, sys.stdout, indent=2, default=str)
        print()
        return 0
    print(f"\n  {ansi.c(vid, ansi.CYAN)} — {ansi.c(record.pop('name'), ansi.BOLD)}")
    record.pop("id", None)
    for key, value in record.items():
        if isinstance(value, dict):
            value = ", ".join(f"{k} {v}" for k, v in value.items() if v is not None)
        elif isinstance(value, (list, tuple)):
            value = (
                " – ".join(map(str, value))
                if key in ("production", "model_years")
                else (", ".join(map(str, value)))
            )
        if value:
            print(f"    {ansi.c(f'{key}:', ansi.DIM)} {value}")
    print()
    return 0


def _assignments(pairs: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"error: expected KEY=VALUE, got {pair!r}")
        out.append((key.strip(), value))
    return out


def _run_edit(message: str, action) -> int:
    from canlib.profile import require_writable_definitions
    from canlib.variants.edit import VariantsEditError

    require_writable_definitions()
    try:
        path = action()
    except VariantsEditError as e:
        raise SystemExit(f"{ansi.c('  Error: ' + str(e), ansi.RED)}") from None
    echo_edit(message, path)
    return 0


def cmd_add(args) -> int:
    from canlib.variants.edit import add_variant

    meta = _assignments(args.meta)
    return _run_edit(f"added variant {args.id}", lambda: add_variant(args.id, args.name, meta))


def cmd_set_meta(args) -> int:
    from canlib.variants.edit import set_variant_meta

    meta = _assignments(args.meta)
    keys = ", ".join(dict.fromkeys(k for k, _ in meta))
    return _run_edit(f"set {keys} on variant {args.id}", lambda: set_variant_meta(args.id, meta))


def cmd_unset_meta(args) -> int:
    from canlib.variants.edit import unset_variant_meta

    keys = ", ".join(args.keys)
    return _run_edit(
        f"removed {keys} from variant {args.id}", lambda: unset_variant_meta(args.id, args.keys)
    )


def cmd_set_revisions(args) -> int:
    from canlib.pids import load_raw_pids
    from canlib.variants.edit import VariantsEditError, set_variant_revisions
    from canlib.variants.revisions import canonical_ecu, declared

    revs = [r.strip().lower() for part in args.revisions for r in part.split(",") if r.strip()]
    if args.clear == bool(revs):
        raise SystemExit(f"{ansi.c('  Error: give the revisions, or --clear', ansi.RED)}")
    ecus = load_raw_pids().get("ecus") or {}
    ecu = canonical_ecu(args.ecu, ecus)
    if ecu is None:
        raise SystemExit(f"{ansi.c(f'  Error: no ECU {args.ecu!r} in ecus/', ansi.RED)}")
    have = declared(ecus[ecu])
    unknown = [r for r in revs if r not in have]
    if unknown:
        raise SystemExit(
            ansi.c(
                f"  Error: {ecu} declares no revision {', '.join(unknown)} "
                f"(declared: {', '.join(have) or 'none'})",
                ansi.RED,
            )
        )
    what = f"[{', '.join(revs)}]" if revs else "any revision"
    try:
        return _run_edit(
            f"variant {args.id}: {ecu} -> {what}",
            lambda: set_variant_revisions(args.id, ecu, None if args.clear else revs),
        )
    except VariantsEditError as e:  # pragma: no cover - _run_edit reports these
        raise SystemExit(str(e)) from None


def cmd_rename(args) -> int:
    from canlib.profile import active
    from canlib.variants.references import rename_everywhere

    written: list = []

    def action():
        path, rewritten = rename_everywhere(args.old, args.new, active())
        written.extend(rewritten)
        return path

    rc = _run_edit(f"renamed variant {args.old} → {args.new}", action)
    for path in written:
        print(f"  {ansi.c('retagged', ansi.DIM)} {path}")
    return rc


def cmd_rm(args) -> int:
    from canlib.profile import active
    from canlib.variants.references import remove_unreferenced

    return _run_edit(f"removed variant {args.id}", lambda: remove_unreferenced(args.id, active()))


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="List and edit the variants of the profile's car (variants.yaml)",
        description="List the variants of the active profile's car — the versions an owner "
        "can name, such as a battery size or a facelift — or edit them "
        "(add/set-meta/unset-meta/set-revisions/rename/rm). A variant can limit which "
        "ECU revisions its car carries and override some profile.yaml settings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument("--json", action="store_true", help="Output the list as JSON")
    sub = parser.add_subparsers(dest="variants_command")

    lst = sub.add_parser("list", help="List the variants (default)")
    lst.add_argument("--json", action="store_true", help="Output as JSON")
    lst.set_defaults(_variants_func=cmd_list)

    show = sub.add_parser("show", help="Show one variant's metadata")
    show.add_argument("id", help="Variant ID (e.g. 28kwh)")
    show.add_argument("--json", action="store_true", help="Output as JSON")
    show.set_defaults(_variants_func=cmd_show)

    meta_help = "Metadata as KEY=VALUE (battery.<field>=…; repeat sources= per source)"
    add = sub.add_parser("add", help="Add a variant")
    add.add_argument("id", help="Variant ID: lower-case, names what differs (e.g. 38kwh)")
    add.add_argument("--name", required=True, help="Human-readable name")
    add.add_argument("meta", nargs="*", metavar="KEY=VALUE", help=meta_help)
    add.set_defaults(_variants_func=cmd_add)

    sm = sub.add_parser("set-meta", help="Set metadata fields on a variant")
    sm.add_argument("id")
    sm.add_argument("meta", nargs="+", metavar="KEY=VALUE", help=meta_help)
    sm.set_defaults(_variants_func=cmd_set_meta)

    um = sub.add_parser("unset-meta", help="Remove metadata fields from a variant")
    um.add_argument("id")
    um.add_argument("keys", nargs="+", metavar="KEY", help="Field (battery.<field> for battery)")
    um.set_defaults(_variants_func=cmd_unset_meta)

    srv = sub.add_parser(
        "set-revisions",
        help="Say which revisions of an ECU this car can carry",
        description="Record which of an ECU's revisions (its revisions: IDs in ecus/) a "
        "variant's car can carry. One revision: that ECU is determined for the car. Several: "
        "a running change, so the ECU decides (pin it with `profile use --pin`). --clear: "
        "any of them (the default).",
    )
    srv.add_argument("id", help="Variant ID")
    srv.add_argument("ecu", metavar="ECU")
    srv.add_argument(
        "revisions", nargs="*", metavar="REVISION", help="Revision IDs (or a comma list)"
    )
    srv.add_argument("--clear", action="store_true", help="Any of the ECU's revisions")
    srv.set_defaults(_variants_func=cmd_set_revisions)

    ren = sub.add_parser(
        "rename",
        help="Change a variant's ID, retagging the captures, logs and ECU files that use it",
    )
    ren.add_argument("old")
    ren.add_argument("new")
    ren.set_defaults(_variants_func=cmd_rename)

    rm = sub.add_parser(
        "rm", help="Remove a variant (refused while captures, logs or ECU files use it)"
    )
    rm.add_argument("id")
    rm.set_defaults(_variants_func=cmd_rm)

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    func = getattr(args, "_variants_func", None)
    if func is None:
        return cmd_list(args)
    return func(args)
