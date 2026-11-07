"""A PID's lifecycle ``status:``, and the visibility rules derived from it.

Every consumer asks these helpers (shipped to the device? swept by a bare-ECU selector?)
instead of comparing status strings, so a new status, or a rule such as "a memory read
never ships", is one edit here.
"""

from __future__ import annotations

from typing import Literal, get_args

from canair.uds.memory import is_memory_request

# ── PID visibility lifecycle ──────────────────────────────────────────────
# A PID's `status:` is a single, mutually-exclusive lifecycle value that
# replaces the old ignored/static/enabled booleans. It answers "where does
# this PID show up?" on four surfaces (tooling index, bare-ECU polling sweep,
# explicit query, and the shipped WiCAN device profile):
#
#   active   (default) — real & live: indexed, swept, queryable, shipped.
#   draft              — discovered/undecoded placeholder or speculative work:
#                        indexed, swept, queryable, but NOT shipped to the device.
#   static             — unchanging identity/calibration block: indexed &
#                        queryable & analysed, but skipped in a bare-ECU sweep
#                        (needs --include-static) and NOT shipped.
#   slow               — real & live but changes too rarely to poll every cycle
#                        (the 21F2 last-cycle summaries): indexed, queryable and
#                        shipped at its own long `period` (required), but skipped in
#                        a bare-ECU sweep (needs --include-static).
#   ignored            — dead/useless DID (NRC, no decodable data): a documented
#                        tombstone excluded from ALL tooling.
#
# Confidence (`verified:`) is a SEPARATE, orthogonal axis and lives per-param.
#
# A closed vocabulary, so it's a `Literal`: every comparison and every write is
# checked statically, with the runtime tuple derived from the type (one source of
# truth for the argparse `choices=` and the editor guards). The ECU schema keeps its
# own `status` enum — `src/canair/schema/` must validate data without importing
# Python — and a drift test asserts the two agree.
PidStatus = Literal["active", "draft", "static", "slow", "ignored"]
PID_STATUSES: tuple[PidStatus, ...] = get_args(PidStatus)
DEFAULT_PID_STATUS: PidStatus = "active"

# The two visibility questions a status answers. Consumers ask these helpers instead of
# comparing against "active"/"static", so a new status is one edit here.
_SHIPPED: frozenset[PidStatus] = frozenset({"active", "slow"})
_UNSWEPT: frozenset[PidStatus] = frozenset({"static", "slow", "ignored"})


def is_shipped(status: PidStatus) -> bool:
    """Whether a PID with this status goes into the generated WiCAN device profile."""
    return status in _SHIPPED


def is_swept(status: PidStatus) -> bool:
    """Whether a bare-ECU selector (``canair monitor ECU``) includes the PID."""
    return status not in _UNSWEPT


def is_snapshot_only(pid_def: dict) -> bool:
    """Whether the ECU serves this DID only inside a DTC snapshot (``snapshot_only: true``).

    Such a PID is defined so its snapshot data decodes, but a live read of it only draws
    an NRC, so nothing polls it and nothing ships it.
    """
    return (pid_def or {}).get("snapshot_only") is True


def pid_ships(pid_def: dict, pid_code: object) -> bool:
    """Whether a PID goes into the generated WiCAN device profile.

    The status and snapshot rules decide, and a memory read (``pid_code`` starting
    ``23``) never ships, whatever its status: the device cannot check the ECU's
    software build before each read, and a reflash moves what an address holds.
    """
    if is_memory_request(pid_code):
        return False
    return is_shipped(pid_status(pid_def)) and not is_snapshot_only(pid_def)


def pid_swept(pid_def: dict) -> bool:
    """Whether a bare-ECU selector includes the PID (status and snapshot rule)."""
    return is_swept(pid_status(pid_def)) and not is_snapshot_only(pid_def)


def pid_status(pid_def: dict) -> PidStatus:
    """Return a PID definition's lifecycle status, defaulting to ``active``.

    Tolerant of unknown/missing values (treated as ``active``) so a malformed
    file degrades to "visible" rather than silently disappearing.
    """
    raw = str((pid_def or {}).get("status", DEFAULT_PID_STATUS)).strip().lower()
    for status in PID_STATUSES:
        if raw == status:
            return status
    return DEFAULT_PID_STATUS
