#!/usr/bin/env python3
"""``canair import`` — bring external CAN data into the profile.

Front door for both data domains, split by kind (the ``uds`` / ``can`` naming
spine — see ``plans/2026-07-24-raw-can-analysis.md``):

- ``import uds <ECU:PID=PAYLOAD>`` — a device-free UDS (domain-A, request/
  response) capture you already have (pasted from a forum/issue, read on another
  tool) → the active profile's ``captures/*.yaml`` via the shared save machinery.
- ``import can <FILE>``  — a raw broadcast-CAN frame log (domain B) (``.asc``/
  ``.blf``/python-can ``.csv``/candump ``.log``/``.trc``/SavvyCAN GVRET) → the
  native ``captures/can/`` store + an ``index.yaml`` entry.
- ``import dbc <FILE>``  — a DBC → the profile's ``signals/`` broadcast
  signal-definition model (domain B), via cantools, with a ``--dry-run`` diff.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from canlib.commands.import_uds import add_uds_parser
from canlib.commands.import_uds import run as _run_uds

NAME = "import"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Import external CAN data (UDS payloads, raw frame logs, DBC) into the profile",
        description=(
            "Import external CAN data into the active profile. Choose a kind:\n\n"
            "  uds   device-free UDS capture (ECU:PID=PAYLOAD, domain A), or every reply\n"
            "        in a diagnostic-bus frame log (--from-log)\n"
            "        -> captures/*.json via the shared save machinery\n"
            "  can   raw broadcast-CAN frame log (.asc/.blf/.csv/candump .log/.trc)\n"
            "        -> captures/can/ native store + index.yaml (domain B)\n"
            "  dbc   DBC signal definitions -> the profile's signals/ model (domain B)\n\n"
            "uds is the domain-A (request/response) half; can/dbc are the domain-B\n"
            "(passive broadcast) half. Frame logs are stored verbatim and indexed (not\n"
            "exploded into the captures/*.yaml schema). SavvyCAN GVRET (.csv) is\n"
            "auto-detected by header."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="import_command", required=True)

    add_uds_parser(sub)

    p_can = sub.add_parser(
        "can",
        help="Import a raw broadcast-CAN frame log into captures/can/",
    )
    p_can.add_argument("file", help="Path to a .asc/.blf/.csv/candump .log/.trc frame log")
    p_can.add_argument(
        "--format",
        choices=["auto", "asc", "blf", "csv", "log", "gvret"],
        default="auto",
        help="Log format (default: auto-detect by extension; .csv is sniffed for GVRET).",
    )
    p_can.add_argument("--label", help="Session label for the index entry")
    p_can.add_argument(
        "--state",
        help="Vehicle power state(s) while logging (comma-separated, e.g. 'driving')",
    )
    p_can.add_argument("--notes", help="Free-text notes for the index entry")
    p_can.add_argument("--source", help="Provenance (capture tool, or upstream URL)")
    p_can.add_argument("--bitrate", type=int, help="Bus bitrate in bit/s (e.g. 500000)")
    p_can.add_argument("--date", help="Log date YYYY-MM-DD (default: from log, else omitted)")
    p_can.add_argument(
        "--force", action="store_true", help="Overwrite an already-imported log of the same name"
    )
    p_can.add_argument("--json", action="store_true", help="Machine-readable result")
    p_can.set_defaults(_import_kind="can", func=run)

    p_dbc = sub.add_parser(
        "dbc",
        help="Import DBC broadcast signal definitions into the signals/ model",
    )
    p_dbc.add_argument("file", help="Path to a .dbc file")
    p_dbc.add_argument("--bus", help="Target bus (signals/<bus>.yaml; default: DBC filename)")
    p_dbc.add_argument("--tx-ecu", dest="tx_ecu", help="Annotate imported messages with this ECU")
    p_dbc.add_argument("--ids", help="Comma-separated arbitration IDs to import (default: all)")
    p_dbc.add_argument("--dry-run", action="store_true", help="Print the diff without writing")
    p_dbc.add_argument("--json", action="store_true", help="Machine-readable result")
    p_dbc.set_defaults(_import_kind="dbc", func=run)

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    kind = getattr(args, "_import_kind", None)
    if kind == "uds":
        return _run_uds(args)
    if kind == "can":
        return _run_can(args)
    if kind == "dbc":
        return _run_dbc(args)
    print("canair import: pick a subcommand (uds | can | dbc). See --help.", file=sys.stderr)
    return 2


def _run_can(args) -> int:
    import json

    from canlib import can_logs
    from canlib.profile import active

    states = [s.strip() for s in (args.state or "").split(",") if s.strip()] or None
    try:
        result = can_logs.import_log(
            Path(args.file),
            active(),
            fmt=args.format,
            label=args.label,
            vehicle_states=states,
            notes=args.notes,
            bitrate=args.bitrate,
            source=args.source,
            date=args.date,
            force=args.force,
        )
    except can_logs.CanLogError as e:
        print(f"import can: {e}", file=sys.stderr)
        return 1

    if args.json:
        json.dump(
            {"stored": str(result.stored_path), "index_entry": result.entry},
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    e = result.entry
    ids = e.get("id_set", [])
    id_preview = ", ".join(ids[:8]) + (f", +{len(ids) - 8} more" if len(ids) > 8 else "")
    print(f"Imported {e['file']} ({e['format']}) -> {result.stored_path}")
    print(
        f"  {e['frame_count']} frames, {len(ids)} distinct IDs" + (f": {id_preview}" if ids else "")
    )
    if e.get("date"):
        print(f"  date: {e['date']}")
    print("  Indexed in captures/can/index.yaml. List with `canair captures can`.")
    return 0


class DbcImportError(Exception):
    """A DBC cannot be mapped onto one ``signals/<bus>.yaml``."""


@dataclass
class DbcImport:
    """What ``import dbc`` makes of a DBC: the messages to merge, and what it leaves out."""

    messages: dict = field(default_factory=dict)
    # mid -> the validator's errors for a message canair can't store (e.g. a signal that
    # overruns its frame, a scale of 0)
    skipped: dict[str, list[str]] = field(default_factory=dict)
    # "mid/signal" for a value table on a scaled or float signal (see _signal_fields)
    value_tables_dropped: list[str] = field(default_factory=list)


def _signal_fields(s, source: str | None) -> tuple[dict, bool]:
    """One cantools signal as ``signals/`` fields, plus whether a value table was dropped.

    A DBC value table (VAL_) becomes ``type: enum`` + ``values`` only on an unscaled
    integer signal: its keys are raw values, while canair's enum keys are the decoded
    value, and the two agree only there.
    """
    fields: dict = {
        "start_bit": int(s.start),
        "length": int(s.length),
        "byte_order": "big" if s.byte_order == "big_endian" else "little",
    }
    if s.is_signed:
        fields["signed"] = True
    if s.is_float:
        fields["float"] = True
    if s.scale is not None and s.scale != 1:
        fields["scale"] = s.scale
    if s.offset:
        fields["offset"] = s.offset
    if s.minimum is not None:
        fields["min"] = s.minimum
    if s.maximum is not None:
        fields["max"] = s.maximum
    if s.unit:
        fields["unit"] = s.unit
    dropped = False
    if s.choices:
        if not s.is_float and s.scale in (None, 1) and not s.offset:
            fields["type"] = "enum"
            fields["values"] = {int(k): str(getattr(v, "name", v)) for k, v in s.choices.items()}
        else:
            dropped = True
    if source:
        fields["source"] = source
    return fields, dropped


def _dbc_to_signals(
    db, id_filter: set[int] | None, tx_ecu: str | None, source: str | None
) -> DbcImport:
    """Map a cantools Database to the linear signals/ import shape.

    ``messages`` is ``{mid: {"name", "tx_ecu"?, "length", "extended"?, "fd"?, "signals":
    {sig: fields}}}``; cantools byte_order (little_endian/big_endian) → little/big and
    ``source`` records provenance on each imported signal. A frame-format flag is
    written only where the ID and length don't already imply it (an extended frame
    numbered at or below 0x7FF; a CAN FD frame of at most 8 bytes). Messages the
    validator would reject are left out, with its reasons, so one malformed message
    doesn't sink a whole real-world DBC.

    Raises :class:`DbcImportError` when the DBC has a standard and an extended frame
    with the same number: ``signals/`` keys messages by number alone, so both cannot
    live on one bus.
    """
    from canlib.validation.signals import check_signals_doc, default_extended

    result = DbcImport()
    chosen = [
        m for m in db.messages if m.signals and (id_filter is None or m.frame_id in id_filter)
    ]
    formats: dict[int, set[bool]] = {}
    for m in chosen:
        formats.setdefault(m.frame_id, set()).add(bool(m.is_extended_frame))
    clashes = sorted(fid for fid, kinds in formats.items() if len(kinds) > 1)
    if clashes:
        ids = ", ".join(f"0x{fid:X}" for fid in clashes)
        raise DbcImportError(
            f"{ids}: a standard and an extended frame share this number, and "
            "signals/<bus>.yaml keys messages by number alone; split the DBC by frame "
            "format and import each part with its own --bus"
        )
    for msg in chosen:
        signals: dict = {}
        for s in msg.signals:
            signals[s.name], dropped = _signal_fields(s, source)
            if dropped:
                result.value_tables_dropped.append(f"0x{msg.frame_id:X}/{s.name}")
        mid = f"0x{msg.frame_id:X}"
        spec: dict = {"name": msg.name, "length": msg.length, "signals": signals}
        if tx_ecu:
            spec["tx_ecu"] = tx_ecu
        if bool(msg.is_extended_frame) != default_extended(msg.frame_id):
            spec["extended"] = bool(msg.is_extended_frame)
        if msg.is_fd and msg.length <= 8:
            spec["fd"] = True
        errors, _ = check_signals_doc({"messages": {mid: spec}})
        if errors:
            result.skipped[mid] = errors
        else:
            result.messages[mid] = spec
    return result


def _run_dbc(args) -> int:
    import json

    try:
        import cantools
    except ImportError:
        print("import dbc: cantools is required (pip install cantools).", file=sys.stderr)
        return 1

    path = Path(args.file)
    if not path.is_file():
        print(f"import dbc: no such file: {path}", file=sys.stderr)
        return 1
    try:
        db = cantools.database.load_file(str(path), strict=False)
    except Exception as e:  # cantools parse errors
        print(f"import dbc: could not parse {path.name}: {e}", file=sys.stderr)
        return 1

    id_filter = None
    if args.ids:
        try:
            id_filter = {int(t.strip(), 16) for t in args.ids.split(",") if t.strip()}
        except ValueError:
            print(f"import dbc: --ids must be hex IDs (got {args.ids!r})", file=sys.stderr)
            return 1
    try:
        result = _dbc_to_signals(db, id_filter, args.tx_ecu, f"dbc:{path.name}")
    except DbcImportError as e:
        print(f"import dbc: {e}", file=sys.stderr)
        return 1
    imported = result.messages
    if not args.json:
        _report_left_out(result)
    if not imported:
        print(
            f"import dbc: no messages to import from {path.name}"
            + (" (after --ids filter)" if args.ids else ""),
            file=sys.stderr,
        )
        return 1

    bus = args.bus or path.stem
    n_sig = sum(len(m["signals"]) for m in imported.values())
    left_out = {
        "skipped": result.skipped,
        "value_tables_dropped": result.value_tables_dropped,
    }

    if args.dry_run:
        if args.json:
            json.dump({"bus": bus, "messages": imported, **left_out}, sys.stdout, indent=2)
            print()
            return 0
        print(
            f"import dbc (dry run) → signals/{bus}.yaml: {len(imported)} messages, {n_sig} signals"
        )
        for mid, m in list(imported.items())[:20]:
            print(
                f"  {mid} {m['name']}: {', '.join(list(m['signals'])[:6])}"
                + (" …" if len(m["signals"]) > 6 else "")
            )
        if len(imported) > 20:
            print(f"  … +{len(imported) - 20} more messages")
        print("Re-run without --dry-run to write.")
        return 0

    from canlib.profile import active
    from canlib.signals_edit import SignalsEditError, merge_bus

    try:
        out, written = merge_bus(bus, imported, profile=active())
    except SignalsEditError as e:
        print(f"import dbc: {e}", file=sys.stderr)
        return 1
    if args.json:
        json.dump(
            {
                "bus": bus,
                "file": str(out),
                "messages": len(imported),
                "signals": written,
                **left_out,
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0
    print(f"Imported {len(imported)} messages ({written} signals) → {out}")
    print("Review/verify with `canair signals list` and `canair validate signals`.")
    return 0


def _report_left_out(result: DbcImport) -> None:
    """Tell the user what the import leaves out (on stderr, so a dry run's diff stays clean)."""
    if result.skipped:
        print(
            f"import dbc: skipping {len(result.skipped)} message(s) canair cannot store:",
            file=sys.stderr,
        )
        for mid, errors in list(result.skipped.items())[:10]:
            print(f"  {mid}: {errors[0]}", file=sys.stderr)
        if len(result.skipped) > 10:
            print(f"  … +{len(result.skipped) - 10} more", file=sys.stderr)
    if result.value_tables_dropped:
        names = ", ".join(result.value_tables_dropped[:5])
        more = len(result.value_tables_dropped) - 5
        print(
            f"import dbc: value tables not carried on {len(result.value_tables_dropped)} "
            f"scaled or float signal(s): {names}" + (f", +{more} more" if more > 0 else ""),
            file=sys.stderr,
        )
