"""``canair sniff`` — passive CAN bus sniffer (raw SLCAN-over-TCP backend).

Opens the WiCAN's SLCAN socket via python-can and shows a live per-ID table
(count / rate / last data / which bytes have changed). Great for discovering
broadcast IDs and periodic signals the request/response ELM327 path can't see.
Optionally logs every frame to a python-can file (``.asc``/``.blf``/``.csv``).

The device must already be in ``slcan`` mode — sniff never switches it. If it's
in another mode, sniff errors with the exact command to fix it
(``canair wican mode set slcan``). See ``canair status``.
"""

from __future__ import annotations

import argparse
import threading
import time

NAME = "sniff"


class SniffStats:
    """Thread-safe per-arbitration-ID aggregation of sniffed CAN frames."""

    def __init__(self):
        self._by_id: dict[int, dict] = {}
        self._lock = threading.Lock()

    def record(self, arb_id: int, data: bytes, ts: float, extended: bool = False) -> None:
        with self._lock:
            e = self._by_id.get(arb_id)
            if e is None:
                self._by_id[arb_id] = {
                    "count": 1,
                    "first": ts,
                    "last": ts,
                    "data": bytes(data),
                    "changed": bytearray(len(data)),
                    "extended": extended,
                }
                return
            e["count"] += 1
            old = e["data"]
            if len(old) == len(data):
                changed = e["changed"]
                for i, (a, b) in enumerate(zip(old, data, strict=False)):
                    if a != b:
                        changed[i] = 1
            else:
                # DLC changed — resize the mask, mark everything volatile.
                e["changed"] = bytearray([1] * len(data))
            e["data"] = bytes(data)
            e["last"] = ts

    def clear(self) -> None:
        with self._lock:
            self._by_id.clear()

    def snapshot(self) -> list[dict]:
        """Return per-ID rows (sorted by ID) with a computed rate in Hz."""
        rows = []
        with self._lock:
            for arb_id, e in sorted(self._by_id.items()):
                span = e["last"] - e["first"]
                hz = (e["count"] - 1) / span if e["count"] > 1 and span > 0 else 0.0
                rows.append(
                    {
                        "id": arb_id,
                        "extended": e["extended"],
                        "count": e["count"],
                        "hz": hz,
                        "data": e["data"],
                        "changed": bytes(e["changed"]),
                    }
                )
        return rows

    @property
    def total_frames(self) -> int:
        with self._lock:
            return sum(e["count"] for e in self._by_id.values())


def render_sniff_table(rows: list[dict]):
    """Render sniff rows as a Rich Table (changed bytes highlighted)."""
    from rich.table import Table
    from rich.text import Text

    table = Table(expand=False, pad_edge=False)
    table.add_column("ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("cnt", justify="right", style="dim")
    table.add_column("Hz", justify="right")
    table.add_column("data (changed bytes highlighted)")

    for r in rows:
        width = 8 if r["extended"] else 3
        id_str = f"{r['id']:0{width}X}"
        data = Text()
        changed = r["changed"]
        for i, b in enumerate(r["data"]):
            volatile = i < len(changed) and changed[i]
            data.append(f"{b:02X}", style="bold yellow" if volatile else "white")
            data.append(" ")
        table.add_row(id_str, str(r["count"]), f"{r['hz']:.1f}", data)
    return table


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Passive CAN sniffer (raw SLCAN mode; live per-ID table)",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair sniff                       # live per-ID table (asks to switch to slcan)
  canair sniff --duration 10 --save bus.asc
  canair sniff --filter 770,7E4 --listen-only
""",
    )
    from canlib.constants import DEFAULT_WICAN, WICAN_ADDRESSES

    parser.add_argument(
        "--wican",
        default=None,
        help=f"WiCAN address: {', '.join(WICAN_ADDRESSES)} or IP "
        f"(default: config transport.host / default_wican={DEFAULT_WICAN})",
    )
    parser.add_argument(
        "--listen-only",
        action="store_true",
        help="Open the bus silently (no ACK/TX) — pure passive sniff",
    )
    parser.add_argument(
        "--filter",
        metavar="IDS",
        default=None,
        help="Comma-separated hex CAN IDs to capture (default: all)",
    )
    parser.add_argument("--duration", type=float, default=None, help="Stop after N seconds")
    parser.add_argument(
        "--save", metavar="FILE", default=None, help="Log all frames to FILE (.asc/.blf/.csv)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Steal the connection lock if one is held"
    )
    parser.set_defaults(func=run)
    return parser


def _parse_filters(spec: str | None) -> list[dict] | None:
    if not spec:
        return None
    ids = [int(x, 16) for x in spec.replace(" ", "").split(",") if x]
    return [{"can_id": i, "can_mask": 0x7FF if i <= 0x7FF else 0x1FFFFFFF} for i in ids]


def _parse_datarate(value) -> int | None:
    """Parse a WiCAN ``can_datarate`` like '500K' / '1M' / '250000' to an int bitrate."""
    if value is None:
        return None
    s = str(value).strip().upper().replace("BIT", "").rstrip("/S")
    try:
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        if s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        return int(s)
    except ValueError:
        return None


def _resolve_device_defaults(host: str | None, port: int | None, bitrate: int | None):
    """Fill port/bitrate from the device's live config when not given on the CLI."""
    if port is not None and bitrate is not None:
        return port, bitrate
    import sys

    from canlib.wican_api import resolve_wican_url
    from canlib.wican_mode import load_config

    cfg = {}
    try:
        cfg = load_config(resolve_wican_url(host))
    except Exception as e:  # best-effort — fall back to conventional defaults
        print(f"  (could not read device config for defaults: {e})", file=sys.stderr)
    if port is None:
        try:
            port = int(cfg.get("port", 3333) or 3333)
        except (TypeError, ValueError):
            port = 3333
    if bitrate is None:
        bitrate = _parse_datarate(cfg.get("can_datarate")) or 500000
    return port, bitrate


def run(args) -> int:
    import sys

    from canlib.lock import WiCANLock
    from canlib.transport import resolve_transport
    from canlib.wican_mode import ModeError, require_protocol

    t = resolve_transport(args)
    host = t.host
    try:
        filters = _parse_filters(args.filter)
    except ValueError:
        print(
            f"error: invalid --filter '{args.filter}' (want hex IDs like 770,7E4)", file=sys.stderr
        )
        return 2

    # Port/bitrate come from the config transport block, falling back to the
    # device's live config (no dedicated CLI flags).
    port, bitrate = _resolve_device_defaults(host, t.port, t.bitrate)
    print(f"  Raw CAN via SLCAN — {host}:{port} @ {bitrate} bps")

    # No auto-switch: the device must already be in slcan mode.
    try:
        require_protocol(host, "slcan", transport_name="slcan-tcp")
    except ModeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    lock = WiCANLock()
    lock.acquire(force=args.force)
    try:
        _run_sniff(host, args, filters, port, bitrate)
    finally:
        lock.release()
    return 0


def _run_sniff(host: str, args, filters, port: int, bitrate: int) -> None:
    import can

    from canlib.transport import SlcanTcpBus

    stats = SniffStats()
    bus = SlcanTcpBus(
        host,
        port=port,
        bitrate=bitrate,
        listen_only=args.listen_only,
        can_filters=filters,
    )

    class _Collector(can.Listener):
        def on_message_received(self, msg: can.Message) -> None:
            stats.record(msg.arbitration_id, bytes(msg.data), msg.timestamp, msg.is_extended_id)

        def stop(self) -> None:
            pass

    listeners: list = [_Collector()]
    logger = can.Logger(args.save) if args.save else None
    if logger:
        listeners.append(logger)

    notifier = can.Notifier(bus, listeners, timeout=1.0)
    try:
        import sys

        if sys.stdout.isatty():
            from canlib.commands._sniff_tui import run_sniff_app

            run_sniff_app(stats, host, duration=args.duration)
        else:
            _run_sniff_plain(stats, duration=args.duration)
    finally:
        notifier.stop()
        if logger:
            logger.stop()
        bus.shutdown()
        _print_summary(stats)


def _run_sniff_plain(stats: SniffStats, duration: float | None) -> None:
    """Non-TTY: capture until duration/Ctrl+C, printing periodic frame counts."""
    import sys

    deadline = time.monotonic() + duration if duration else None
    try:
        while deadline is None or time.monotonic() < deadline:
            time.sleep(1.0)
            print(
                f"  sniffing… {len(stats.snapshot())} IDs, {stats.total_frames} frames",
                file=sys.stderr,
            )
    except KeyboardInterrupt:
        pass


def _print_summary(stats: SniffStats) -> None:
    from rich.console import Console

    rows = stats.snapshot()
    console = Console()
    console.print(f"\n  Sniffed {len(rows)} unique IDs, {stats.total_frames} frames.")
    if rows:
        console.print(render_sniff_table(rows))
    else:
        console.print(
            "  [dim]No frames seen. The bus may be idle, or — common on OBD-II — the "
            "port is gateway-isolated: ECUs answer diagnostic requests but no broadcast "
            "traffic is forwarded, so there is nothing to sniff passively.[/dim]"
        )
