"""Live monitor over the raw CAN transport (SLCAN + client-side ISO-TP).

Uses :class:`RawUdsClient` with request pipelining + per-ECU multi-DID batching
instead of the ELM327 WebSocket terminal. The device must already be in ``slcan``
mode (the caller / ``run_raw`` verifies this — no auto-switching).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from canair.render import messages

from ...transport.config import ConnectionOptions, TransportConfig
from ..session import SessionOptions
from .options import MonitorOptions

if TYPE_CHECKING:
    from canair.ecus.pids import EcuIndexEntry
    from canair.uds.addressing import EcuAddress


def query_ecu_addresses(
    query_steps: list[dict], ecu_index: Mapping[str, EcuIndexEntry]
) -> dict[str, EcuAddress]:
    """name(upper) -> resolved :class:`EcuAddress` for every ECU in the query steps."""
    out: dict[str, EcuAddress] = {}
    for step in query_steps:
        ecu = step["ecu"].upper()
        info = ecu_index.get(ecu)
        if info:
            out[ecu] = info["address"]
    return out


def build_raw_client(
    host: str,
    port: int,
    bitrate: int,
    ecus: dict[str, EcuAddress],
    conn: ConnectionOptions,
    pids_data: dict,
):
    """Construct a connected :class:`RawUdsClient` (SLCAN bus + client-side ISO-TP).

    The single build path shared by the initial monitor connect and the
    mid-session reconnect, so both apply the same timeouts / ISO-TP config.
    Raises ``OSError`` if the SLCAN socket can't be opened.
    """
    from canair.safety.blocklist import SessionPolicy
    from canair.transport.timeouts import cli_timeout, ecu_timeouts_by_name

    from ...transport import RawUdsClient, SlcanTcpBus

    bus = SlcanTcpBus(host, port=port, bitrate=bitrate)
    cli = cli_timeout(conn.timeout)
    return RawUdsClient(
        bus,
        ecus,
        timeout=(cli if cli is not None else 3.0),
        ecu_timeouts=(None if cli is not None else ecu_timeouts_by_name(pids_data)),
        isotp_config=pids_data.get("isotp"),
        session_policy=SessionPolicy.from_profile(pids_data),
    )


def build_raw_reconnector(
    candidates: Sequence[TransportConfig],
    ecus: dict[str, EcuAddress],
    conn: ConnectionOptions,
    pids_data: dict,
):
    """A :class:`MonitorReconnector` that re-homes to a reachable slcan-tcp device.

    Restricts the candidate list to raw (slcan-tcp) devices and reuses
    :func:`build_raw_client` as the per-candidate connect step (built off-thread,
    since opening the SLCAN socket blocks).
    """
    import asyncio

    from .reconnect import MonitorReconnector, reconnect_policy

    raw_candidates = [c for c in candidates if c.is_raw]

    async def connect(cand: TransportConfig):
        assert cand.host is not None  # wait_for_reachable only yields hosted candidates
        port, bitrate = cand.resolve_device_defaults(pids_data.get("can_bitrate"))
        return await asyncio.to_thread(
            build_raw_client, cand.host, port, bitrate, ecus, conn, pids_data
        )

    return MonitorReconnector(raw_candidates, connect, reconnect_policy(wait=conn.wait))


async def run_raw_monitor(
    monitor: MonitorOptions,
    host: str,
    port: int,
    bitrate: int,
    pids_data: dict,
    *,
    conn: ConnectionOptions,
    session: SessionOptions,
    candidates: Sequence[TransportConfig] = (),
) -> int:
    """Run the live monitor over raw CAN. Assumes the device is in slcan mode."""
    from canair.ecus.pids import build_ecu_index

    from .run import mode_monitor

    ecu_index = build_ecu_index(pids_data)
    ecus = query_ecu_addresses(monitor.query_steps, ecu_index)
    if not ecus:
        messages.error("no known ECUs in the query steps.")
        return 1

    print(
        f"  Raw CAN monitor via SLCAN — {host}:{port} @ {bitrate} bps  "
        f"(ECUs: {', '.join(sorted(ecus))})",
        file=sys.stderr,
    )
    try:
        client = build_raw_client(host, port, bitrate, ecus, conn, pids_data)
    except OSError as e:
        from canair.core.oserrors import connect_error_detail

        messages.error(
            f"can't connect to the SLCAN port at {host}:{port} — {connect_error_detail(e)}"
        )
        return 1
    # The monitor reconciles its --save journal in its own finally even on a
    # disconnect (so no data loss), then re-raises the transport error — catch it
    # here through the shared classifier so a dropped bus is a clean message, not
    # a traceback. (The ELM monitor gets this via run_session_guarded; the raw
    # monitor bypasses it for its pipelined client, so guard here.)
    from ...transport.errors import describe_transport_error, transport_error_types

    try:
        await mode_monitor(
            None,
            monitor.query_steps,
            pids_data,
            monitor.verbose,
            raw_client=client,
            reconnect=build_raw_reconnector(candidates, ecus, conn, pids_data),
            **monitor.mode_kwargs(),
        )
    except transport_error_types() as e:
        messages.error(
            describe_transport_error(e, host=host, transport_label="SLCAN", saving=session.saving)
        )
        return 1
    if session.timings:
        from canair.transport.timing import print_timings

        print_timings(client.timings, as_json=session.as_json)
    return 0
