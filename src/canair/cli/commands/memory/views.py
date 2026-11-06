"""The offline ``canair memory`` subcommands: ls, show, diff, export and set."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from canair.cli.commands._edit import add_edit_args, run_edit
from canair.cli.profile_context import active, target_profile
from canair.core.edit_errors import EditError
from canair.ecus.names import resolve_tx
from canair.memory.diff import diff_dumps
from canair.memory.export import to_bin, to_ihex, to_srec
from canair.memory.store import DumpError, iter_dumps, resolve_dump, store_root
from canair.render import messages
from canair.uds.memory import parse_format
from canair.yaml_edit.memory import set_memory

from .reads import address_ranges, memory_format
from .render import print_diff, print_dump_summary, print_hexdump, print_listing


def _root() -> Path:
    return store_root(active().name)


def _dump(ref: str):
    try:
        return resolve_dump(_root(), ref)
    except DumpError as e:
        messages.fail(str(e), code=2)


def cmd_ls(args: argparse.Namespace) -> int:
    print_listing(list(iter_dumps(_root(), args.ecu)), as_json=args.json)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    dump = _dump(args.dump)
    if args.range is None:
        print_dump_summary(dump, as_json=args.json)
    else:
        (start, end), *_ = args.range
        print_hexdump(dump, start, end, as_json=args.json)
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    a, b = _dump(args.a), _dump(args.b)
    try:
        diff = diff_dumps(a, b, min_run=args.min_run)
    except DumpError as e:
        messages.fail(str(e), code=2)
    print_diff(diff, as_json=args.json)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    dump = _dump(args.dump)
    try:
        if args.format == "bin":
            _first, data = to_bin(dump, None if args.fill is None else int(args.fill, 16))
            payload: bytes = data
        else:
            payload = (to_ihex(dump) if args.format == "ihex" else to_srec(dump)).encode()
    except DumpError as e:
        messages.fail(str(e), code=2)
    if args.output is None or args.output == "-":
        sys.stdout.buffer.write(payload)
        sys.stdout.flush()
        return 0
    out = Path(args.output).expanduser()
    out.write_bytes(payload)
    print(f"  ✓ Wrote {out} (holds ECU memory: keep it local)", file=sys.stderr)
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    tx = resolve_tx(args.ecu, profile=active())
    if tx is None:
        raise EditError(f"could not resolve ECU {args.ecu!r}")
    fmt = parse_format(args.format) if args.format else None
    if fmt is None and args.max_block is None and args.session is None and not args.clear:
        raise EditError("give --format, --max-block, --session or --clear")
    return run_edit(
        lambda: set_memory(
            tx,
            fmt=fmt,
            max_block=args.max_block,
            session=args.session,
            clear=args.clear or (),
            ecus_dir=args.dir,
            profile=target_profile(args),
        ),
        f"{args.ecu} memory updated",
        args=args,
    )


def add_offline_parsers(kinds) -> None:
    ls = kinds.add_parser("ls", help="List the dumps (offline)")
    ls.add_argument("ecu", nargs="?", metavar="ECU", help="Only this ECU's dumps")
    ls.add_argument("--json", action="store_true", help="Output as JSON")
    ls.set_defaults(func=cmd_ls)

    show = kinds.add_parser("show", help="Summarise a dump, or hex-dump a range of it")
    show.add_argument("dump", metavar="DUMP", help="A dump id (from `memory ls`), prefix or path")
    show.add_argument("--range", type=address_ranges, metavar="START-END", help="Hex-dump this")
    show.add_argument("--json", action="store_true", help="Output as JSON")
    show.set_defaults(func=cmd_show)

    diff = kinds.add_parser(
        "diff",
        help="The address runs whose bytes differ between two dumps of one ECU",
        description="Compare two dumps of one ECU and list the runs of addresses whose bytes "
        "differ, with both values. Dumps of two software builds are refused: a reflash moves "
        "what an address holds.",
    )
    diff.add_argument("a", metavar="DUMP_A")
    diff.add_argument("b", metavar="DUMP_B")
    diff.add_argument(
        "--min-run", type=int, default=1, help="Only runs of at least N changed bytes"
    )
    diff.add_argument("--json", action="store_true", help="Output as JSON")
    diff.set_defaults(func=cmd_diff)

    export = kinds.add_parser(
        "export",
        help="Write a dump as Intel HEX, S-record or raw binary",
        description="Write a dump for other tools. Intel HEX and S-record keep its holes; a "
        "raw binary needs --fill for one. The file holds ECU memory: keep it local.",
    )
    export.add_argument("dump", metavar="DUMP")
    export.add_argument("--format", choices=("ihex", "srec", "bin"), default="ihex")
    export.add_argument("--fill", metavar="HEX", help="bin: the byte to write into holes")
    export.add_argument("-o", "--output", metavar="FILE", help="Output file (default stdout)")
    export.set_defaults(func=cmd_export)

    st = kinds.add_parser(
        "set",
        help="Set an ECU's memory format, largest block or session (writes ecus/)",
        description="Set the memory: section of an ECU: the format its reads take, the "
        "largest block that comes back whole and the session they need. `canair scan "
        "memory` writes the same fields from what it finds.",
    )
    st.add_argument("ecu", metavar="ECU")
    st.add_argument(
        "--format", type=memory_format, metavar="ALFID|kwp", help="UDS ALFID (e.g. 24) or kwp"
    )
    st.add_argument("--max-block", type=int, metavar="N", help="Largest read in bytes")
    st.add_argument("--session", metavar="HEX", help='Session mode the reads need ("03")')
    st.add_argument(
        "--clear",
        action="append",
        choices=("format", "max_block", "session"),
        help="Drop a field (repeatable)",
    )
    add_edit_args(st)
    st.set_defaults(func=cmd_set)
