"""Readers for the ``transport:`` config block: reconnect, ping, ELM327 and firmware probe."""

from __future__ import annotations

import math
from typing import Literal

from canair.config.file import load_config

# Bounded reconnect window (seconds) for a mid-session monitor drop when the user
# did NOT pass --wait. --wait retries forever instead; see wait_for_reachable.
# Sized for a mobile link rather than a LAN: a hotspot handing over between cells,
# or a VPN re-keying, is routinely out for tens of seconds, and a recording that
# gives up after 6s of that loses the rest of the drive. The cost of waiting is
# only latency before an eventual failure, so it is the cheaper error.
_DEFAULT_RECONNECT_MAX_WAIT = 60.0


# WebSocket keepalive ping interval/timeout (seconds) for the wican-ws terminal.
# Without it a half-open link (a hotspot NAT timeout, a VPN re-key) never raises —
# reads just time out forever and the monitor shows every signal as stale with no
# way to notice the socket is dead. Generous enough for a cellular+VPN round-trip;
# 0 disables (`transport.ws_ping_interval`).
_DEFAULT_WS_PING_INTERVAL = 20.0


# Consecutive monitor cycles in which no signal produced a fresh value before the
# session is declared dead and re-homed (`transport.stale_cycles_before_reconnect`;
# 0 disables). This is the catch-all for desyncs the transport can't self-detect —
# notably multi-DID batches, which carry no echo to validate.
_DEFAULT_STALE_CYCLES = 3


# Whether an ELM327 request may carry an expected-response-count digit, letting
# the adapter return the moment a reply is complete instead of waiting out its
# ATST budget (`transport.expected_responses`). Measured ~4x faster on a WiCAN
# Pro; on by default because a count is learned from a completed plain read and
# any mismatch self-heals into a plain retry. Turn it off to pin an adapter to
# the plain request form. Only the ELM327 transports read it — the raw path runs
# its own ISO-TP and has no adapter to instruct.
_DEFAULT_EXPECTED_RESPONSES = True


# Whether live commands ask a WiCAN which firmware generation it runs before using
# a 4.x-only interface (`transport.firmware_check`; src/canair/device/wican/firmware.py). Off
# skips the probe entirely, for users who know what they're running; "always" also
# probes elm327-tcp hosts, which are usually clones with no HTTP server.
_DEFAULT_FIRMWARE_CHECK = True


def reconnect_max_wait() -> float:
    """Bounded reconnect window (seconds) for a mid-session monitor drop.

    Read from ``transport.reconnect_max_wait`` (default 6.0). This bounds the
    automatic reconnect attempt when the user did *not* pass ``--wait``; with
    ``--wait`` the monitor retries indefinitely instead. A non-positive or
    non-finite value falls back to the default.
    """
    raw_block = load_config().get("transport")
    block = raw_block if isinstance(raw_block, dict) else {}
    try:
        value = float(block.get("reconnect_max_wait", _DEFAULT_RECONNECT_MAX_WAIT))
        if not math.isfinite(value) or value <= 0:
            return _DEFAULT_RECONNECT_MAX_WAIT
    except (TypeError, ValueError):
        return _DEFAULT_RECONNECT_MAX_WAIT
    return value


def ws_ping_interval() -> float | None:
    """WebSocket keepalive ping interval (seconds), or ``None`` when disabled.

    Read from ``transport.ws_ping_interval`` (default 20.0; ``0`` disables). The
    ``wican-ws`` terminal needs this to notice a *half-open* link: with pings off,
    a link that has silently died (hotspot NAT eviction, VPN re-key) is
    indistinguishable from an ECU that simply isn't answering, so the monitor
    reports stale signals indefinitely instead of reconnecting. The WiCAN's
    ``/ws`` handler does not claim control frames, so ESP-IDF's HTTP server
    answers the ping itself. A negative or non-finite value falls back to the
    default.
    """
    raw_block = load_config().get("transport")
    block = raw_block if isinstance(raw_block, dict) else {}
    try:
        value = float(block.get("ws_ping_interval", _DEFAULT_WS_PING_INTERVAL))
    except (TypeError, ValueError):
        return _DEFAULT_WS_PING_INTERVAL
    if not math.isfinite(value) or value < 0:
        return _DEFAULT_WS_PING_INTERVAL
    return None if value == 0 else value


def stale_cycles_before_reconnect() -> int:
    """Consecutive all-stale monitor cycles that trigger a reconnect (0 = never).

    Read from ``transport.stale_cycles_before_reconnect`` (default 3). A cycle is
    "all stale" when every polled signal failed to produce a fresh value, so the
    display is showing nothing but carried-forward history. That is the symptom of
    a desynchronised or dead session, and unlike a transport error it raises
    nothing — without this threshold the monitor waits forever. Deliberately a
    *cycle* count, not a duration: it scales with the poll rate.
    """
    raw_block = load_config().get("transport")
    block = raw_block if isinstance(raw_block, dict) else {}
    try:
        value = int(block.get("stale_cycles_before_reconnect", _DEFAULT_STALE_CYCLES))
    except (TypeError, ValueError):
        return _DEFAULT_STALE_CYCLES
    return value if value >= 0 else _DEFAULT_STALE_CYCLES


def expected_responses() -> bool:
    """Whether ELM327 requests may carry an expected-response-count digit.

    Read from ``transport.expected_responses`` (default True). Appending the
    frame count to an odd-length request lets the adapter stop listening as soon
    as the reply is whole, instead of waiting out ``ATST`` to be sure no further
    frame is coming — the dominant per-request cost on the ELM327 transports.
    Set it false to keep every request in the plain form, e.g. for an adapter
    that answers ``?`` to the extra nibble on requests canair has not yet
    learned to opt out of.
    """
    raw_block = load_config().get("transport")
    block = raw_block if isinstance(raw_block, dict) else {}
    value = block.get("expected_responses", _DEFAULT_EXPECTED_RESPONSES)
    return _DEFAULT_EXPECTED_RESPONSES if value is None else bool(value)


def firmware_check() -> bool:
    """Whether the firmware probe is on at all (see :func:`firmware_check_mode`)."""
    return firmware_check_mode() != "off"


FirmwareCheckMode = Literal["off", "on", "always"]


def firmware_check_mode() -> FirmwareCheckMode:
    """How eagerly live commands probe a device's WiCAN firmware generation.

    Read from ``transport.firmware_check`` (default ``true``):

    * ``true`` (``on``) — probe the WiCAN transports (``slcan-tcp``, ``wican-ws``)
      and the ``canair wican`` device commands, so canair can refuse or warn on
      WiCAN firmware v6, which dropped most of the 4.x interfaces;
    * ``always`` — also probe ``elm327-tcp`` hosts, which are usually generic
      clones with no HTTP server, for the v6 shared-chip note;
    * ``false`` (``off``) — never probe.
    """
    raw_block = load_config().get("transport")
    block = raw_block if isinstance(raw_block, dict) else {}
    value = block.get("firmware_check", _DEFAULT_FIRMWARE_CHECK)
    if value is None:
        value = _DEFAULT_FIRMWARE_CHECK
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "always":
            return "always"
        return "off" if text in ("false", "no", "off", "0") else "on"
    return "on" if value else "off"
