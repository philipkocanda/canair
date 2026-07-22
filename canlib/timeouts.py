"""Response-timeout resolution.

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


def cli_timeout(args) -> float | None:
    """The user's explicit ``--timeout`` in seconds, or None when not given."""
    t = getattr(args, "timeout", None)
    return float(t) if t is not None else None


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
