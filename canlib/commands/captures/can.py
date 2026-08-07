"""The ``captures can`` kind — list imported raw broadcast-CAN frame logs.

Domain B's read surface: the logs themselves stay in their native format under
``captures/can/``, indexed by ``captures/can/index.yaml``; this lists that index.
Ingest is ``canair import can`` (:mod:`canlib.commands.import_`).
"""

import argparse

from canlib import ansi


def cmd_can_logs(as_json: bool = False) -> int:
    """List imported raw broadcast-CAN frame logs from captures/can/index.yaml."""
    import json as _json

    from canlib import can_logs
    from canlib.profile import active

    logs = can_logs.list_logs(active())
    if as_json:
        print(_json.dumps(logs, indent=2))
        return 0
    if not logs:
        print(
            "  No imported CAN frame logs (captures/can/ is empty). Add one with "
            "`canair import can <FILE>`."
        )
        return 0
    print(
        f"\n  {ansi.BOLD}Raw-CAN frame logs{ansi.RESET} {ansi.DIM}({len(logs)} in captures/can/){ansi.RESET}"
    )
    for e in logs:
        ids = e.get("id_set", [])
        meta = []
        if e.get("date"):
            meta.append(e["date"])
        if e.get("vehicle_states"):
            meta.append(",".join(e["vehicle_states"]))
        if e.get("bitrate"):
            meta.append(f"{e['bitrate']}bps")
        meta_str = f"  {ansi.DIM}[{' · '.join(meta)}]{ansi.RESET}" if meta else ""
        label = f"  {e['label']}" if e.get("label") else ""
        print(
            f"    {ansi.CYAN}{e.get('file', '?')}{ansi.RESET} {ansi.DIM}({e.get('format', '?')}){ansi.RESET}"
            f"{meta_str}{label}"
        )
        print(
            f"      {ansi.DIM}{e.get('frame_count', 0)} frames, {len(ids)} IDs"
            + (f": {', '.join(ids[:10])}{', …' if len(ids) > 10 else ''}" if ids else "")
            + ansi.RESET
        )
    print()
    return 0


def _add_can_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "can",
        help="List imported raw broadcast-CAN frame logs (captures/can/index.yaml)",
        description="List imported raw broadcast-CAN frame logs (domain B) — "
        "file/format/frames/IDs per log. Import them with `canair import can`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.set_defaults(func=lambda args: cmd_can_logs(as_json=args.json))
    return parser
