"""``canair ecu add``: register an ECU offline."""

from __future__ import annotations

import argparse
from pathlib import Path

from canair.cli.args.hexarg import parse_hex_arg
from canair.cli.commands._edit import edit_scope
from canair.cli.profile_context import target_profile
from canair.core.edit_errors import EditError
from canair.render import messages
from canair.yaml_edit.echo import echo_edit


def add_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "add",
        help="Register a new ECU in the active profile (offline; no device)",
        description="Register a new ECU as ecus/<name>.yaml in the active profile.\n\n"
        "Offline counterpart to `canair discover --register` (which needs a live "
        "bus): use this to seed a known ECU into a blank profile — e.g. one shared "
        "with another model-year — ready for contributions. The write is validated "
        "and comment-preserving.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair ecu add 7C6 --name CLU --description 'Cluster (instrument panel)'\n"
        "  canair ecu add 0x7E4 --name BMS --id-protocol KWP2000\n"
        "  canair ecu add 0x704 --name BMS --rx-id 0x784   # non-standard response addr\n"
        "  canair ecu add 0x18DB33F1 --name EVC --mode normal_29bit --rx-id 0x18DAF1DB --fc-id 0x18DADBF1\n"
        "  canair ecu add 770 --name IGPM --notes 'Seeded offline; no PIDs yet'\n",
    )
    parser.add_argument("tx", metavar="TX", help="ECU TX id (hex, e.g. 7C6 or 0x7C6)")
    parser.add_argument("--name", help="ECU short name (default: Unknown-<TX>)")
    parser.add_argument("--description", help="Human description")
    parser.add_argument(
        "--id-protocol", dest="id_protocol", help="Identity protocol (UDS | KWP2000)"
    )
    parser.add_argument(
        "--rx-id",
        dest="rx_id",
        help="CAN response address override (hex, e.g. 0x784) — for an ECU whose "
        "response addr isn't tx_id + the profile's addressing.rx_offset",
    )
    parser.add_argument(
        "--mode",
        help="Addressing mode (normal_11bit | normal_29bit | normal_fixed_29bit | "
        "normal_extended_11bit | extended_29bit) — required to seed a 29-bit ECU",
    )
    parser.add_argument(
        "--target-address",
        dest="target_address",
        help="ISO-TP target extension byte (hex) — extended-11-bit/29-bit modes",
    )
    parser.add_argument(
        "--source-address",
        dest="source_address",
        help="ISO-TP tester (source) byte (hex, default 0xF1) — extended-11-bit modes",
    )
    parser.add_argument(
        "--fc-id",
        dest="fc_id",
        help="Flow-control arbitration override (hex) — functional-TX / physical-RX ECUs",
    )
    parser.add_argument("--notes", help="Free-text notes")
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite existing identity fields"
    )
    parser.add_argument(
        "--dir", type=Path, default=None, help="ecus/ directory (default: active profile)"
    )
    parser.set_defaults(func=cmd_add)
    return parser


def cmd_add(args) -> int:
    from canair.yaml_edit.ecu import find_ecu_file_by_tx, register_ecu, tx_key

    try:
        tx_id = int(str(args.tx), 16)
    except ValueError:
        raise EditError(f"invalid TX id {args.tx!r} — expected hex (e.g. 7C6)") from None

    rx_id = parse_hex_arg(getattr(args, "rx_id", None), "rx-id")
    target_address = parse_hex_arg(getattr(args, "target_address", None), "target-address")
    source_address = parse_hex_arg(getattr(args, "source_address", None), "source-address")
    fc_id = parse_hex_arg(getattr(args, "fc_id", None), "fc-id")

    fields = {
        k: v
        for k, v in (
            ("description", args.description),
            ("id_protocol", args.id_protocol),
            ("notes", args.notes),
        )
        if v is not None
    }
    with edit_scope(args):
        wrote = register_ecu(
            tx_id,
            name=args.name,
            overwrite=args.overwrite,
            ecus_dir=args.dir,
            rx_id=rx_id,
            mode=getattr(args, "mode", None),
            target_address=target_address,
            source_address=source_address,
            fc_id=fc_id,
            profile=target_profile(args),
            **fields,
        )

    disp = tx_key(tx_id)
    label = args.name or f"Unknown-{tx_id:03X}"
    if wrote:
        fpath = find_ecu_file_by_tx(tx_id, args.dir, profile=target_profile(args))
        if fpath is not None:
            echo_edit(f"registered {label} ({disp})", fpath)
    else:
        messages.note(f"{label} ({disp}) already registered; nothing to change")
    return 0
