"""The ``captures can`` kind — list imported raw broadcast-CAN frame logs.

Domain B's read surface: the logs themselves stay in their native format under
``captures/can/``, indexed by ``captures/can/index.yaml``; this lists that index.
Ingest is ``canair import can`` (:mod:`canlib.commands.import_`).
"""

import argparse

from .query import _BOLD, _CYAN, _DIM, _RESET


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
    print(f"\n  {_BOLD}Raw-CAN frame logs{_RESET} {_DIM}({len(logs)} in captures/can/){_RESET}")
    for e in logs:
        ids = e.get("id_set", [])
        meta = []
        if e.get("date"):
            meta.append(e["date"])
        if e.get("vehicle_states"):
            meta.append(",".join(e["vehicle_states"]))
        if e.get("bitrate"):
            meta.append(f"{e['bitrate']}bps")
        meta_str = f"  {_DIM}[{' · '.join(meta)}]{_RESET}" if meta else ""
        label = f"  {e['label']}" if e.get("label") else ""
        print(
            f"    {_CYAN}{e.get('file', '?')}{_RESET} {_DIM}({e.get('format', '?')}){_RESET}"
            f"{meta_str}{label}"
        )
        print(
            f"      {_DIM}{e.get('frame_count', 0)} frames, {len(ids)} IDs"
            + (f": {', '.join(ids[:10])}{', …' if len(ids) > 10 else ''}" if ids else "")
            + _RESET
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
