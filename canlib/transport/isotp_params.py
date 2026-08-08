"""Client-side ISO-TP parameters, profile-configurable.

Both raw-CAN clients (:class:`~canlib.transport.raw_terminal.RawTerminal` and
:class:`~canlib.transport.uds_raw.RawUdsClient`) drive the bus with python-can +
``can-isotp``. The ISO-TP flow-control / padding / CAN-FD parameters used to be
hard-coded identically in both; they now live here so the two clients share one
definition *and* a profile can override them per vehicle.

The defaults reproduce the historical, Ioniq-verified behaviour (11-bit, no
flow-control block/STmin gap, 8-byte classic CAN frames padded with ``0xAA``), so
an existing profile that carries no ``isotp:`` block behaves exactly as before. A
profile whose ECUs pad with a different byte, run CAN-FD, or need a different
flow-control cadence sets those under ``isotp:`` in ``profile.yaml``.
"""

from __future__ import annotations

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

# Names of the accepted fields (for schema/validation reuse).
ISOTP_FIELDS: tuple[str, ...] = tuple(_DEFAULTS)


def build_isotp_params(config: dict | None = None) -> dict[str, int | bool]:
    """Build a ``can-isotp`` params dict, overlaying a profile ``isotp:`` block.

    ``config`` is the profile's ``isotp:`` mapping (or ``None``). Each recognised,
    non-``None`` key overrides its default; missing/unknown keys keep the default
    (validation flags unknown keys — this stays lenient at runtime).
    """
    params = dict(_DEFAULTS)
    if config:
        for key in _DEFAULTS:
            value = config.get(key)
            if value is not None:
                params[key] = value
    return params


def resolve_tx_padding(pids_data: dict | None) -> int:
    """The profile's ISO-TP padding byte (``isotp.tx_padding``, default 0xAA).

    The byte ECUs pad short frames with — Hyundai/Kia use ``0xAA``, but other
    makes use ``0x00``/``0xCC``. Callers that strip trailing padding from a
    reassembled payload read it from here rather than assuming ``0xAA``.
    """
    if isinstance(pids_data, dict):
        isotp = pids_data.get("isotp")
        if isinstance(isotp, dict):
            value = isotp.get("tx_padding")
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0xFF:
                return value
    return DEFAULT_TX_PADDING
