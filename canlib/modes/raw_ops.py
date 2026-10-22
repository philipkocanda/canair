"""Raw-CAN command dispatch (transport = ``slcan-tcp``).

When the configured transport is raw, live commands run over python-can + our
client-side ISO-TP instead of the ELM327 WebSocket. The device must already be
in ``slcan`` mode — we verify and error clearly, never switch.

- ``monitor`` uses the optimized :class:`RawUdsClient` path (request pipelining
  across ECUs + per-ECU multi-DID batching).
- Everything else (query, raw, scan, discover, identity, iocontrol, routines,
  and the ``*-scan`` probers) runs the normal ELM-path dispatch over a
  :class:`~canlib.transport.raw_terminal.RawTerminal` adapter, which speaks
  ISO-TP under the same ``set_header`` / ``send_uds`` interface the modes expect.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..transport.config import TransportConfig
    from ..wican_firmware import FirmwareInfo


@dataclass(frozen=True)
class SlcanTarget:
    """Where a raw SLCAN session connects, once :func:`slcan_preflight` cleared it."""

    host: str
    port: int
    bitrate: int
    firmware: FirmwareInfo


def slcan_preflight(
    transport: TransportConfig, profile_bitrate: int | None, *, listen_only: bool = False
) -> SlcanTarget:
    """Check a ``slcan-tcp`` device is usable before any session opens on it.

    Shared by the raw live commands and ``canair sniff``. Raises
    :class:`~canlib.wican_mode.ModeError` with the message to print (a refusal
    included). One TCP check of the HTTP port runs first and gates every HTTP
    request after it — the firmware probe, the protocol check, the device-config
    read — so an offline device costs one connect timeout before the fail-fast
    SLCAN message rather than one per request.
    """
    from ..config import fallback_settings
    from ..wican_firmware import (
        UNKNOWN,
        compatibility,
        detect,
        print_verdict,
        probes,
        unconfirmed,
    )
    from ..wican_mode import ModeError, _tcp_open, require_protocol, require_slcan_reachable

    host = transport.host
    if not host:
        raise ModeError("transport has no host configured.")

    _, connect_timeout, _ = fallback_settings()
    http_up = transport.is_wican_http and _tcp_open(host, 80, connect_timeout)
    firmware = detect(host, "slcan-tcp") if http_up else UNKNOWN
    v6 = firmware.generation == "v6"

    if listen_only:
        verdict = compatibility(firmware, "sniff-listen-only")
        if verdict.refused:
            raise ModeError(verdict.message)
        # An HTTP server that didn't identify itself may still be a v6 WiCAN.
        if http_up and firmware.generation == "unknown":
            why = "unidentified" if probes("sniff-listen-only") else "check-off"
            print_verdict(unconfirmed("sniff-listen-only", why))
    print_verdict(compatibility(firmware, "slcan-tcp"))

    # Explicit-mode policy: a 4.x device must already be serving SLCAN. v6 has no
    # protocol mode (nor /load_config) to check, so it got a setup warning above.
    if http_up and not v6:
        require_protocol(host, "slcan", transport_name="slcan-tcp")
    port, bitrate = transport.resolve_device_defaults(
        profile_bitrate, query_device=http_up and not v6
    )
    # The SLCAN *data* port is distinct from the HTTP API: a wedged or silent
    # socket would otherwise surface as a bare connect timeout deep in the stack.
    require_slcan_reachable(host, port, firmware=firmware, http_ok=http_up)
    return SlcanTarget(host, port, bitrate, firmware)


async def run_raw(args, transport, pids_data) -> int:
    """Entry point for a live command over a raw (slcan-tcp) transport."""
    from ..wican_mode import ModeError

    try:
        target = slcan_preflight(transport, pids_data.get("can_bitrate"))
    except ModeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        return await _run_session(args, target, pids_data)
    finally:
        if getattr(args, "reboot", False):
            from ..wican_api import reboot_after_session

            reboot_after_session(transport)


async def _run_session(args, target: SlcanTarget, pids_data) -> int:
    host, port, bitrate = target.host, target.port, target.bitrate

    # Monitor: optimized pipelined + batched backend.
    if args.multi and args.monitor:
        from .raw_monitor import run_raw_monitor

        return await run_raw_monitor(args, host, port, bitrate, pids_data)

    # All other commands: reuse the shared dispatch over a RawTerminal adapter.
    from ..addressing import AddressBook
    from ..quirks import HK_F1XX_MINUS_ONE, has_quirk
    from ..safety import SessionPolicy
    from ..timeouts import cli_timeout, ecu_timeouts_by_tx
    from ..transport import RawTerminal
    from .dispatch import run_session_guarded

    print(f"  Raw CAN via SLCAN — {host}:{port} @ {bitrate} bps")
    cli = cli_timeout(args)
    # Resolved tx->EcuAddress map (per-ECU mode/rx_id/extended/FC bytes) so
    # RawTerminal builds each ISO-TP stack from the full addressing; unknown TX
    # ids (a discovery sweep) fall back to the profile's rx_offset/mode.
    addresses = AddressBook.from_profile(pids_data)
    try:
        terminal = RawTerminal(
            host,
            port,
            bitrate,
            verbose=args.verbose,
            unsafe=getattr(args, "unsafe", False),
            timeout=(cli if cli is not None else 2.0),
            isotp_config=pids_data.get("isotp"),
            addr_map=dict(addresses.by_tx),
            rx_offset=addresses.rx_offset,
            mode=addresses.mode,
            hk_f1xx_offset=has_quirk(pids_data, HK_F1XX_MINUS_ONE),
        )
    except OSError as e:
        # The pre-flight check above should catch an unreachable port, but guard
        # the connect itself too (a race, or the port closing between probe and
        # connect) so a socket failure is a clean message, not a traceback.
        from ..transport.errors import connect_error_detail

        print(
            f"error: can't connect to the SLCAN port at {host}:{port} — {connect_error_detail(e)}",
            file=sys.stderr,
        )
        return 1
    # Per-ECU budgets apply only when the user didn't force --timeout.
    if cli is None:
        terminal.ecu_timeouts = ecu_timeouts_by_tx(pids_data)
    terminal.session_policy = SessionPolicy.from_profile(pids_data)
    try:
        return await run_session_guarded(args, terminal, pids_data, host, transport_label="SLCAN")
    finally:
        await terminal.close()
