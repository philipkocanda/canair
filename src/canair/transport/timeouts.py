"""Response-timeout resolution, and how long to wait for (or repeat) a slow or busy ECU.

The effective per-request UDS response budget is resolved with this precedence:

    --timeout (CLI, global)  >  per-ECU response_timeout_ms  >  transport default

The global ``--timeout`` (seconds) overrides everything for the whole run. When
it is *not* given, each ECU may still carry its own ``response_timeout_ms`` (in
``ecus/<name>.yaml``, milliseconds) so slow modules (e.g. the VCU/MCU powertrain
ECUs) get a longer budget while fast chassis ECUs stay snappy. With neither set,
the transport's own default applies.

Note: the profile-wide ``response_timeout_ms`` continues to drive the *ELM327
ATST* (the dongle's CAN-side wait) — it is intentionally NOT reused as the raw
ISO-TP recv budget, since that value (≈614 ms on the Ioniq) is much tighter than
the multi-frame reassembly budget the raw path needs and would cause spurious
timeouts. Per-ECU budgets are the knob for lengthening a specific ECU's wait.
"""

from __future__ import annotations

from typing import Final

from canair.safety.actuation import READ, classify
from canair.uds.nrc import NRC_BUSY_REPEAT_REQUEST
from canair.uds.protocol import DiagProtocol
from canair.uds.response import UdsResponse

# UDS ResponsePending (NRC 0x78): the ECU acknowledged the request but needs more time,
# and sends the final reply in a later frame. Every transport waits through it: up to
# P2*server_max (5000 ms, the ISO 14229-2 default) after each pending frame, and for a
# whole run of them at most PENDING_TOTAL_TIMEOUT, so an ECU that keeps answering
# "pending" cannot hold an exchange open for ever.
PENDING_RECV_TIMEOUT: Final = 5.0
PENDING_TOTAL_TIMEOUT: Final = 20.0


def pending_deadline(now: float, wait: float, cap: float | None) -> tuple[float, float]:
    """The deadline after a ResponsePending frame at ``now``, and the cap of its run.

    ``wait`` is how long the next frame may take; ``cap`` is None at the run's first
    pending frame. The run may last ``PENDING_TOTAL_TIMEOUT``, or ``wait`` when that is
    longer (an explicit ``--timeout`` is never cut short by the cap).
    """
    if cap is None:
        cap = now + max(PENDING_TOTAL_TIMEOUT, wait)
    return min(now + wait, cap), cap


def cli_timeout(timeout: float | None) -> float | None:
    """The user's explicit ``--timeout`` in seconds, or None when not given."""
    return float(timeout) if timeout is not None else None


def ecu_timeouts_by_tx(pids_data: dict) -> dict[int, float]:
    """``{tx_id: seconds}`` from per-ECU ``response_timeout_ms`` (empty if none)."""
    out: dict[int, float] = {}
    for info in (pids_data.get("ecus") or {}).values():
        ms = info.get("response_timeout_ms")
        tx = info.get("tx_id")
        if ms and tx is not None:
            out[int(tx)] = float(ms) / 1000.0
    return out


def ecu_timeouts_by_name(pids_data: dict) -> dict[str, float]:
    """``{ECU_NAME(upper): seconds}`` from per-ECU ``response_timeout_ms``."""
    out: dict[str, float] = {}
    for name, info in (pids_data.get("ecus") or {}).items():
        ms = info.get("response_timeout_ms")
        if ms:
            out[name.upper()] = float(ms) / 1000.0
    return out


# busyRepeatRequest (NRC 0x21): ISO 14229-1 has the client repeat a request the server
# answered with it. The server is alive but cannot take the request now, often a gateway
# forwarding another tester's traffic, and read as final the refusal makes a scan record an id
# as refused. Both terminal families ask busy_retry_delay before they return a negative reply.
# Only a request canair.safety.actuation.classify reads as "read" is repeated: one that acts
# may have been half done, and the transport must not send it twice.
#: The wait before each repeat, in seconds. Three repeats at most, 0.7 s in all.
BUSY_RETRY_DELAYS: Final = (0.1, 0.2, 0.4)


def busy_retry_delay(
    resp: UdsResponse, request: str, protocol: DiagProtocol, repeats: int
) -> float | None:
    """How long to wait before repeating ``request``, or None to return ``resp`` as it is.

    ``repeats`` is how many times the request was already repeated for being busy.
    """
    if resp.get("nrc") != NRC_BUSY_REPEAT_REQUEST or repeats >= len(BUSY_RETRY_DELAYS):
        return None
    try:
        if classify(request, protocol) != READ:
            return None
    except ValueError:
        return None
    return BUSY_RETRY_DELAYS[repeats]
