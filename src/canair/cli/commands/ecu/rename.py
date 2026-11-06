"""``canair ecu rename``: rename an ECU."""

from __future__ import annotations

import argparse
from pathlib import Path

from canair.analysis.ecu_summary import list_records
from canair.cli.commands._edit import edit_scope
from canair.cli.commands._hints import ecu_completer as _ecu_completer
from canair.cli.profile_context import active, target_profile
from canair.ecus.names import resolve_tx
from canair.ecus.pids import load_pids
from canair.ecus.registry import load_ecus
from canair.yaml_edit.echo import echo_edit
from canair.yaml_edit.ecu import rename_ecu, tx_key
from canair.yaml_edit.revisions import retag_ecu_rename

from .show import unknown_ecu


def add_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "rename",
        help="Rename an ECU (rewrites its key and ecus/ file)",
        description="Rename an ECU in the active profile.\n\n"
        "An ECU's name is its top-level YAML key plus its ecus/<name>.yaml "
        "filename, so this rewrites the key and moves the file. The write is "
        "validated and comment-preserving. Use it to "
        "promote a placeholder (e.g. Unknown-7D5) to a real name once identified.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair ecu rename Unknown-7D5 EPB\n"
        "  canair ecu rename 0x7D5 EPB          # resolve the old ECU by hex id\n",
    )
    parser.add_argument(
        "ecu", metavar="ECU", help="ECU to rename: current name, alias, or hex TX/RX id"
    ).completer = _ecu_completer
    parser.add_argument("new_name", metavar="NEW_NAME", help="New ECU short name")
    parser.add_argument(
        "--dir", type=Path, default=None, help="ecus/ directory (default: active profile)"
    )
    parser.set_defaults(func=cmd_rename)
    return parser


def cmd_rename(args) -> int:
    tx_id = resolve_tx(args.ecu, profile=active())
    if tx_id is None:
        return unknown_ecu(
            args.ecu,
            list_records(
                load_ecus(profile=active()), load_pids(profile=active()), profile=active()
            ),
        )

    old_name = (load_ecus(profile=active()).get(tx_id) or {}).get("name")
    with edit_scope(args):
        new_path = rename_ecu(tx_id, args.new_name, ecus_dir=args.dir, profile=target_profile(args))

    echo_edit(f"renamed {args.ecu} → {args.new_name} ({tx_key(tx_id)})", new_path)
    if args.dir is None and old_name and old_name != args.new_name:
        # Variants' revisions: lists and capture revision tags are keyed by ECU name.
        for path in retag_ecu_rename(old_name, args.new_name.strip(), active()):
            echo_edit("retagged revisions", path)
    return 0
