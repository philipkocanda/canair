"""TypedDicts for the on-disk capture-file shapes (``captures/*.json``).

The persisted capture store is machine-written by the ``canlib.captures``
builders / the journal-reconcile path and read on every history-consuming
command. These TypedDicts mirror ``canlib/schema/captures_schema.json`` 1:1 so
the shape is enforced by the ``ty`` type checker (a wrong key or a missing
required field fails the build) — the schema stays the runtime source of truth,
these are its static-typing companion.

Scope: the on-disk shapes, plus :class:`CaptureEntry` — the flattened
*in-memory* row that ``load_all_captures()`` produces (session metadata joined
onto each capture, with the on-disk ``rx`` address resolved to a short ``ecu``
name). That row is what every history-consuming command actually reads, so it is
modelled here rather than passed around as a bare ``dict``.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from .keepmode import EntryKeepMode, PersistedKeepMode


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
    ``resyncs`` is not a response category — it counts transport realignments
    (recoveries) and is likewise only present when non-zero.
    """

    exchanges: int
    drop: NotRequired[int]
    stale: NotRequired[int]
    no_data: NotRequired[int]
    bus: NotRequired[int]
    decode: NotRequired[int]
    other: NotRequired[int]
    resyncs: NotRequired[int]


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
    # Wall-clock UDS round-trip in ms (transport + ECU, not pure ECU time).
    # Recorded only for single per-DID reads on the live query path; absent for
    # batched multi-DID reads, monitor --save, scans, and imports. Comparable
    # only within the same session `transport`.
    elapsed_ms: NotRequired[int]


class SessionMeta(TypedDict, total=False):
    """The optional metadata fields of a :class:`CaptureSession`.

    Split out so a builder can *accumulate* only the fields it actually has —
    every write checked against this shape — and then splat them into the session
    literal (``{"date": …, "label": …, **meta, "captures": …}``). That yields a
    fully-typed :class:`CaptureSession` while preserving the schema's on-disk field
    order, which a single typed literal can't express (a conditional key can't
    appear in a literal, and appending after construction would put the large
    ``captures`` array before the small metadata fields).

    The alternative the builders used before was an untyped ``dict`` plus
    ``cast(CaptureSession, …)`` at return — which type-checked nothing at all: a
    write of ``session["keep_mode"] = "all"`` (a value the schema forbids) passed
    silently. :class:`CaptureSession` inherits these fields rather than repeating
    them, so the two can't drift.
    """

    vehicle_states: list[str]
    notes: str
    # Only the two dedup policies are recordable; `all`/`last` applied no dedup,
    # so the field is omitted rather than claiming one (see keepmode.py).
    keep_mode: PersistedKeepMode
    transport: str
    quality: Quality


class CaptureSession(SessionMeta):
    """One recording session: metadata + its list of captures.

    Required fields here; the optional metadata is inherited from
    :class:`SessionMeta` (a ``total=False`` base keeps its keys optional).
    """

    date: str
    label: str
    captures: list[CaptureRecord]
    version: NotRequired[str]


class CaptureFile(TypedDict):
    """A whole ``captures/YYYY-MM-DD.json`` document."""

    sessions: list[CaptureSession]


class CaptureEntry(TypedDict):
    """One flattened ``(session, capture)`` row from ``load_all_captures()``.

    The read-side counterpart to the on-disk shapes above: each session's metadata
    is denormalised onto every capture so the analysis commands
    (``decode``/``correlate``/``hunt``/``investigate``/``align``) can filter and
    time-align without walking the file structure.

    Naming: ``ecu`` is the resolved canonical **short name** (``"IGPM"``), while
    the on-disk field is the CAN **response address** under ``rx`` — kept here as
    ``ecu_addr``. ``session_*`` keys carry the owning session's metadata;
    ``label``/``notes`` are the capture's own.

    ``_session_idx``/``_capture_idx`` locate the row inside its source ``file``,
    which is how in-place note edits and deletes address a capture.

    Consumers that only *read* rows should accept ``Sequence[CaptureEntry]``, not
    ``list[CaptureEntry]``: ``list`` is invariant, so a ``list`` parameter refuses
    a ``list[CaptureEntry]`` argument wherever the annotation says ``list[dict]``,
    which makes the type impossible to adopt incrementally.
    """

    file: str
    date: str
    # -- the owning session's metadata, denormalised onto every row --
    session_label: str
    session_version: str
    vehicle_states: list[str]
    session_notes: str
    keep_mode: EntryKeepMode  # "" when the session recorded no dedup policy
    transport: str
    quality: Quality | None
    # -- the capture itself --
    ecu: str  # resolved short name ("" when the rx address is unknown)
    ecu_addr: str  # raw CAN response address, e.g. "0x7EC"
    pid: str | int
    payload: str | None
    response: str | None
    scan_results: ScanResults | None
    notes: str
    time: str
    label: str
    # -- locator within ``file`` (for in-place edit/delete) --
    _session_idx: int
    _capture_idx: int
