"""TypedDicts for the on-disk capture-file shapes (``captures/*.json``).

The persisted capture store is machine-written by the ``canlib.captures``
builders / the journal-reconcile path and read on every history-consuming
command. These TypedDicts mirror ``canlib/schema/captures_schema.json`` 1:1 so
the shape is enforced by the ``ty`` type checker (a wrong key or a missing
required field fails the build) — the schema stays the runtime source of truth,
these are its static-typing companion.

Scope: the **on-disk** shapes only. The in-memory entry dict produced by
``load_all_captures()`` (which resolves the ``rx`` address to a short ``ecu``
name) is a different, richer shape and is intentionally not modelled here.

See ``plans/2026-07-28-captures-rx-field-rename-and-typing.md``.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class RespondingEntry(TypedDict):
    """One responder in a scan's ``scan_results.responding`` list.

    Keyed by ``did`` (DID/service scans) or ``rx`` (broadcast/discover scans) —
    the schema requires at least one of the two, which TypedDict can't express,
    so both are optional here and the ``anyOf`` is enforced by the JSON schema.
    """

    response: str
    did: NotRequired[str]
    rx: NotRequired[str]  # CAN response address of the originating ECU
    notes: NotRequired[str]


class ScanResults(TypedDict, total=False):
    """The ``scan_results`` payload of a scan/discover capture."""

    responding: list[RespondingEntry]
    rejected: str
    notes: str


class Quality(TypedDict):
    """Transport data-quality footprint recorded at capture time.

    ``exchanges`` is always present; the error categories appear only when
    non-zero (NRCs are legitimate ECU answers and are not counted here).
    """

    exchanges: int
    drop: NotRequired[int]
    stale: NotRequired[int]
    no_data: NotRequired[int]
    bus: NotRequired[int]
    decode: NotRequired[int]
    other: NotRequired[int]


class CaptureRecord(TypedDict):
    """One capture: an ECU response address (``rx``) + PID and exactly one payload field.

    The schema requires exactly one of ``payload`` / ``response`` /
    ``scan_results`` (a ``oneOf`` TypedDict can't express); all three are
    NotRequired here and the constraint is enforced by the JSON schema.
    """

    rx: str  # CAN response address (RX = request TX + 8), e.g. "0x7EC"; or "broadcast"
    pid: str | int
    payload: NotRequired[str]
    response: NotRequired[str]
    scan_results: NotRequired[ScanResults]
    label: NotRequired[str]
    time: NotRequired[str]
    notes: NotRequired[str]


class CaptureSession(TypedDict):
    """One recording session: metadata + its list of captures."""

    date: str
    label: str
    captures: list[CaptureRecord]
    vehicle_states: NotRequired[list[str]]
    notes: NotRequired[str]
    keep_mode: NotRequired[str]
    transport: NotRequired[str]
    quality: NotRequired[Quality]


class CaptureFile(TypedDict):
    """A whole ``captures/YYYY-MM-DD.json`` document."""

    sessions: list[CaptureSession]
