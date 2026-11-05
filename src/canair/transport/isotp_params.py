"""Client-side ISO-TP parameters, profile-configurable.

Both raw-CAN clients (:class:`~canair.transport.raw.terminal.RawTerminal` and
:class:`~canair.transport.raw.uds_client.RawUdsClient`) drive the bus with python-can +
``can-isotp``. The ISO-TP flow-control / padding / CAN-FD parameters used to be
hard-coded identically in both; they now live here so the two clients share one
definition *and* a profile can override them per vehicle.

The defaults reproduce the historical, Ioniq-verified behaviour (11-bit, no
flow-control block/STmin gap, 8-byte classic CAN frames padded with ``0xAA``), so
an existing profile that carries no ``isotp:`` block behaves exactly as before. A
profile that pads its requests with a different byte, whose ECUs pad their replies
with one (``rx_padding``, read by canair only), that runs CAN-FD, or that needs a
different flow-control cadence sets those under ``isotp:`` in ``profile.yaml``. One
ECU that needs its own flow control (a pump that sends no consecutive frame after
BlockSize 0) sets the :data:`ECU_ISOTP_FIELDS` under ``isotp:`` in its ``ecus/`` file.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from canair.core.eventlog import log_event

# Byte an unspecified profile transmits as ISO-TP padding (Hyundai/Kia pad with
# 0xAA). Exposed so callers/tests can reference the historical default by name.
DEFAULT_TX_PADDING = 0xAA

# Largest single ISO-TP message, from the 12-bit length field of a First Frame.
# Practically unreachable for a UDS *request*, so a client that segments has no
# batch-size ceiling worth worrying about — unlike an ELM327, which fits one frame.
ISOTP_MAX_REQUEST_BYTES = 0xFFF

# Every tunable + its historical default. The key set is also the whitelist of
# accepted ``isotp:`` fields (see canair validate) — an unknown key is a typo.
_DEFAULTS: dict[str, int | bool] = {
    "tx_padding": DEFAULT_TX_PADDING,  # ISO-TP fill byte for a short frame
    "blocksize": 0,  # FC BlockSize (0 = send all consecutive frames)
    "stmin": 0,  # FC SeparationTime minimum (0 = as fast as possible)
    "rx_flowcontrol_timeout": 1000,  # ms to wait for a flow-control frame
    "rx_consecutive_frame_timeout": 1000,  # ms to wait for the next CF
    "can_fd": False,  # classic CAN by default; True for CAN-FD ECUs
    "tx_data_length": 8,  # frame data length (8 classic; up to 64 on CAN-FD)
}

# Fields canair reads itself and never hands to can-isotp, which has no notion of
# the byte a *reply* is padded with. `rx_padding` defaults to `tx_padding` (see
# `resolve_rx_padding`), so a profile that set only `tx_padding` keeps its behaviour.
_CANAIR_ONLY_FIELDS: tuple[str, ...] = ("rx_padding",)

# Names of the accepted fields (for schema/validation reuse).
ISOTP_FIELDS: tuple[str, ...] = (*_DEFAULTS, *_CANAIR_ONLY_FIELDS)

# Fields one ECU's own ``isotp:`` block may override. Only those the raw path applies per ISO-TP
# stack: an ELM327 adapter keeps one flow-control setting for the session (`ATFCSD`) and pads
# its requests itself, the reply padding strips every multi-DID batch the same way, and CAN-FD
# is a property of the bus. A profile-wide value an ECU does not override still applies to it.
ECU_ISOTP_FIELDS: tuple[str, ...] = (
    "blocksize",
    "rx_flowcontrol_timeout",
    "rx_consecutive_frame_timeout",
)

# The two budgets above wait for a frame that has to cross the network, so on a
# remote link the round trip is spent *inside* them. They are widened by the
# measured link budget rather than replaced by it: the configured value is the
# ECU's share and the measurement is the network's, and the two delays add.
_LINK_SCALED_FIELDS = ("rx_flowcontrol_timeout", "rx_consecutive_frame_timeout")

# Link budget above which a non-zero flow-control BlockSize is worth a word. Below
# it the extra round trips are cheap enough not to be worth a log line.
_BLOCKSIZE_WARN_BUDGET_S = 0.1


def build_isotp_params(
    config: dict | None = None,
    link_budget: float | None = None,
    ecu_config: Mapping[str, Any] | None = None,
) -> dict[str, int | bool]:
    """Build a ``can-isotp`` params dict, overlaying a profile ``isotp:`` block.

    ``config`` is the profile's ``isotp:`` mapping (or ``None``). Each recognised,
    non-``None`` key overrides its default; missing/unknown keys keep the default
    (validation flags unknown keys — this stays lenient at runtime). ``ecu_config``
    is one ECU's own ``isotp:`` block, laid over the profile's for the
    :data:`ECU_ISOTP_FIELDS` only.

    ``link_budget`` is the measured network round-trip allowance in *seconds*
    (see :class:`~canair.transport.link_latency.LinkLatency`). When given, it is added to
    the two flow-control budgets. Without it a LAN-tuned 1 s wait is spent on the
    network alone on a hotspot-behind-a-VPN link, the stack gives up on a
    multi-frame response mid-reassembly, and the largest PIDs are the first to
    fail — which is how the same symptom kept being read as a flaky ECU.
    """
    params = dict(_DEFAULTS)
    for block, fields in ((config, _DEFAULTS), (ecu_config, ECU_ISOTP_FIELDS)):
        if block:
            for key in fields:
                value = block.get(key)
                if value is not None:
                    params[key] = value
    if link_budget and link_budget > 0.0:
        extra = int(link_budget * 1000.0)
        for key in _LINK_SCALED_FIELDS:
            params[key] = int(params[key]) + extra
        _warn_blocksize_cost(int(params["blocksize"]), link_budget)
    return params


def _warn_blocksize_cost(blocksize: int, link_budget: float) -> None:
    """Note that a non-zero BlockSize buys a round trip per block on a slow link.

    ``blocksize: 0`` asks the ECU to send every consecutive frame after a single
    flow-control frame, so one round trip covers the whole response. Any non-zero
    value makes canair send a fresh flow-control frame every N frames, and on a
    remote link each of those is a full round trip that a local setup never pays —
    a large response can take seconds. Worth saying once, since the setting is
    usually inherited from a profile written on a LAN.
    """
    if blocksize <= 0 or link_budget < _BLOCKSIZE_WARN_BUDGET_S:
        return
    log_event(
        "config",
        f"isotp.blocksize={blocksize} costs one round trip per {blocksize} frames, and "
        f"this link is budgeted at {link_budget * 1000.0:.0f}ms per round trip; blocksize 0 sends the whole "
        "response after a single flow-control frame",
        level=logging.INFO,
    )


def ecu_isotp_blocks(pids_data: Mapping[str, Any] | None) -> dict[int, Mapping[str, Any]]:
    """``{tx_id: isotp block}`` for every registered ECU that carries its own ``isotp:``.

    What the raw transports take to build each ECU's stack from the profile's block plus its
    own (:func:`build_isotp_params`). An ECU without one, or an unregistered id, gets the
    profile's alone.
    """
    out: dict[int, Mapping[str, Any]] = {}
    ecus = pids_data.get("ecus") if isinstance(pids_data, Mapping) else None
    for ecu_def in (ecus or {}).values():
        if not isinstance(ecu_def, Mapping):
            continue
        block, tx_id = ecu_def.get("isotp"), ecu_def.get("tx_id")
        if isinstance(block, Mapping) and block and isinstance(tx_id, int):
            out[tx_id] = block
    return out


def _padding_byte(pids_data: dict | None, key: str) -> int | None:
    """``isotp.<key>`` when it is a byte, else None."""
    if isinstance(pids_data, dict):
        isotp = pids_data.get("isotp")
        if isinstance(isotp, dict):
            value = isotp.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0xFF:
                return value
    return None


def resolve_rx_padding(pids_data: dict | None) -> int:
    """The byte ECU replies are padded with, and so the byte to strip from them.

    ``isotp.rx_padding``; when absent, ``isotp.tx_padding`` (the one byte both
    meant before the two were separated), else ``0xAA``. Hyundai/Kia's ES95486
    has them differ: requests padded with ``0x55``, responses with ``0xAA``. The
    byte canair *sends* is ``tx_padding`` in :func:`build_isotp_params`, which only
    the raw ``slcan-tcp`` path uses (an ELM327 pads its requests itself).
    """
    value = _padding_byte(pids_data, "rx_padding")
    if value is None:
        value = _padding_byte(pids_data, "tx_padding")
    return DEFAULT_TX_PADDING if value is None else value
