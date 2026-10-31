"""YAML PID/ECU data loading and index building.

Each vehicle profile stores its ECU definitions as one YAML file per ECU under
``<profile>/ecus/``. Every file is the single source of truth for one ECU:
identity, probe history (``scan_log``), DTC meanings (``dtcs``), and all
readable/actuatable data (``pids``/``iocontrol``/``routines``). Profile-wide
settings (``car_model``, ``init``, ``failure_types``, ...) live one level up in
``<profile>/profile.yaml``.
"""

import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal, TypedDict, get_args

from canlib import yaml_io
from canlib.actuation import default_release, normalize_request
from canlib.addressing import AddressingMode, EcuAddress
from canlib.safety import SessionProtocol, session_protocol

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
# truth for the argparse `choices=` and the editor guards). The schema keeps its
# own `valid_pid_status:` list — `canlib/schema/` must validate data without
# importing Python — and a drift test asserts the two agree.
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


# Parameter fields a PID can state once for all its parameters. A parameter that sets the key
# itself (even to ``""`` / ``[]`` / ``false``, an explicit "none") keeps its own value; one that
# omits it inherits the PID's. In parameter order. ``notes`` is deliberately absent: a PID's
# notes describe the page, a parameter's describe the signal, and neither stands in for the other.
INHERITED_PARAM_FIELDS: tuple[str, ...] = (
    "unit",
    "ha_class",
    "min",
    "max",
    "source",
    "source_links",
    "verified",
)


def effective_param(pid_def: dict, param_def: dict) -> dict:
    """The parameter with the PID's shared fields filled in where it sets none of its own.

    Returns ``param_def`` itself when there is nothing to inherit, else a copy — the
    definition on file is never modified.
    """
    inherited = {
        field: pid_def[field]
        for field in INHERITED_PARAM_FIELDS
        if param_def.get(field) is None and (pid_def or {}).get(field) is not None
    }
    return {**param_def, **inherited} if inherited else param_def


def _inherit_pid_fields(ecus: dict[str, Any]) -> None:
    """Resolve every parameter's inherited fields in place, so readers never see the gap."""
    for ecu_def in ecus.values():
        pids = ecu_def.get("pids") if isinstance(ecu_def, dict) else None
        for pid_def in (pids or {}).values():
            params = pid_def.get("parameters") if isinstance(pid_def, dict) else None
            if not isinstance(params, dict):
                continue
            for name, param in params.items():
                if isinstance(param, dict):
                    params[name] = effective_param(pid_def, param)


class PidIndexEntry(TypedDict):
    """One PID's entry in a :func:`build_ecu_index` result.

    ``shipped``/``swept`` are derived from ``status`` (single source of truth) so
    callers never re-implement the lifecycle rules — see ``PID_STATUSES``.
    """

    parameters: dict[str, Any]
    period: int | None  # the PID's declared poll period (ms); None when it declares none
    status: PidStatus
    shipped: bool  # include in the generated WiCAN device profile (see is_shipped)
    swept: bool  # include in a bare-ECU sweep (see is_swept)
    support_map: Any  # raw `support_map:` declaration or None (see canlib.uds.support_map)


class EcuIndexEntry(TypedDict):
    """One ECU's entry in a :func:`build_ecu_index` result.

    ``rx_id`` is the resolved CAN response address (explicit per-ECU ``rx_id`` →
    profile ``addressing.rx_offset`` → the conventional ``tx_id + 0x08``), so the
    raw transport never recomputes ``tx + 8``. ``mode`` is the resolved CAN
    addressing mode (per-ECU ``addressing.mode`` → profile → 11-bit), which shapes
    the ISO-TP stack the raw transport builds for this ECU. ``address`` is the
    full resolved :class:`~canlib.addressing.EcuAddress` (mode + RX + any extended
    target/source bytes + flow-control override) the transport builds the stack
    from; ``rx_id``/``mode`` are surfaced flat for the many callers that only need
    those two.
    """

    tx_id: int
    rx_id: int
    mode: AddressingMode
    address: EcuAddress
    pids: dict[str, PidIndexEntry]
    multi_did: bool
    multi_did_max: int
    wake: Any  # WakePlan | None — profile-declared wake ritual (canlib.wake)


class IoControlCommand(TypedDict):
    """One actuator command in a :func:`build_iocontrol_index` result.

    ``on``/``off`` are the full request hex strings (UDS ``0x2F``, or KWP2000
    ``0x30`` on ECUs that speak it); either may be
    ``""`` when this DID has no simple command for that direction (common for
    ShortTermAdjustment actuators needing value bytes, and for every
    scanner-discovered entry). Callers must treat an empty string as "refuse",
    never as a reason to fall through to the other direction.

    ``session_mode`` is the DiagnosticSessionControl sub-function (two upper-case
    hex digits) opened when ``session`` is true.

    ``release`` is the request that hands the output back to the ECU, sent on exit,
    on Ctrl-C and by ``--poll``: the entry's declared ``release:``, else the one a
    UDS ``0x2F`` request implies (``2F {DID} 00``), else ``""``. A KWP2000 ``0x30``
    command implies none (:mod:`canlib.actuation`), so it has a release only when
    the profile declares one, and ``canair io`` refuses its ``on`` until then.

    ``discovery`` marks an entry merged in from ``iocontrol_discoveries:``
    (probed, not curated) rather than the hand-verified ``iocontrol:`` section.
    """

    label: str
    on: str
    off: str
    release: str
    session: bool
    session_mode: str
    hold: bool
    verified: bool
    notes: str
    status_param: str | None
    discovery: bool


class IoControlIndexEntry(TypedDict):
    """One ECU's entry in a :func:`build_iocontrol_index` result."""

    tx_id: int
    protocol: SessionProtocol
    cmds: dict[str, IoControlCommand]


class RoutineIndexEntry(TypedDict):
    """One ECU's entry in a :func:`build_routines_index` result."""

    tx_id: int
    routines: dict[str, dict[str, Any]]


class SessionIndexEntry(TypedDict):
    """One ECU's entry in a :func:`build_sessions_index` result."""

    tx_id: int
    sessions: dict[str, dict[str, Any]]


def _yaml_load(fh) -> dict:
    return yaml_io.safe_load(fh)


# ── per-process memoization ───────────────────────────────────────────────
# The active profile's ECU dir is parsed once per process and reused. Writers
# (canlib.pids_edit / canlib.ecus.edit) call clear_cache() after mutating a
# file so a later read in the same process never sees stale data.
_cache: dict[str, dict] = {}

# Caches *derived* from these definitions, registered by their owning module.
# Clearing the definitions without clearing what was built from them leaves the
# derived copy authoritative and wrong — e.g. a decode preview that still names
# the previous profile's parameters. Each hook drops its own cache.
_derived_cache_hooks: list[Callable[[], None]] = []


def register_derived_cache(clear: Callable[[], None]) -> None:
    """Register a cache built from ECU definitions, to clear with them.

    Idempotent, so a lazily-built cache may register on every (re)build.
    """
    if clear not in _derived_cache_hooks:
        _derived_cache_hooks.append(clear)


def clear_cache() -> None:
    """Drop the memoized ECU-definition load and everything derived from it.

    Call after writing any ECU file, or after switching the active profile.
    """
    _cache.clear()
    _resolution_cache.clear()
    for clear in _derived_cache_hooks:
        clear()


# Revision resolutions, keyed like _cache (a resolution depends on the same inputs).
_resolution_cache: dict[str, dict] = {}
# Profiles (by cache key) whose skipped revision-gated definitions were already noted.
_noted_skips: set[str] = set()


def load_pids(path: Path | None = None) -> dict:
    """Load ECU/PID definitions from a profile's ``ecus/`` directory.

    Accepts either a directory (``ecus/``) containing per-ECU YAML files, or a
    single legacy YAML file. When ``path`` is None, the active vehicle profile's
    definitions are returned **resolved for the selected car** — only the ECUs it is
    fitted with, each at its resolved revision (see :mod:`canlib.variants.revisions`),
    with ``profile.yaml`` as the variant overrides it — and memoized per process. A
    ``path`` reads the files as written; :func:`load_raw_pids` does the same for the
    active profile.
    """
    if path is None:
        from .profile import active

        prof = active()
        key = prof.cache_key
        cached = _cache.get(key)
        if cached is not None:
            return cached
        result = _resolved(prof)
        _cache[key] = result
        return result

    path = Path(path)
    if path.is_dir():
        from .profile import profile_for_path

        meta_file = profile_for_path(path).meta_file
        meta = {}
        if meta_file.exists():
            with open(meta_file) as f:
                meta = _yaml_load(f) or {}
        return _load_dir(path, meta=meta)

    # Legacy: single file
    with open(path) as f:
        return _yaml_load(f)


def load_raw_pids(profile=None) -> dict:
    """The active (or given) profile's definitions as written: no variant, no revision.

    For the variant-aware readers — ``validate``, the editors, and anything that must
    see every revision at once. Memoized like :func:`load_pids`.
    """
    from .profile import active, read_meta

    prof = profile or active()
    key = f"{prof.root}|raw"
    cached = _cache.get(key)
    if cached is None:
        cached = _load_dir(prof.ecus_dir, meta=read_meta(prof.meta_file))
        _cache[key] = cached
    return cached


def _selected_variant_def(prof):
    from canlib.variants import VariantsError, load_variants
    from canlib.variants.tags import selected_variant

    from .profile import ProfileError

    vid = selected_variant(prof)
    if vid is None:
        return None
    try:
        return load_variants(prof).get(vid)
    except VariantsError as e:
        raise ProfileError(str(e)) from None


def revision_resolutions(profile=None) -> dict:
    """ECU name -> :class:`~canlib.variants.revisions.Resolution`, for the selected car.

    Only ECUs that declare revisions appear. Raises :class:`~canlib.profile.ProfileError`
    when a pin names an ECU or revision that cannot be.
    """
    from canlib.variants.revisions import RevisionError, resolve

    from .profile import ProfileError, active

    prof = profile or active()
    key = prof.cache_key
    cached = _resolution_cache.get(key)
    if cached is not None:
        return cached
    ecus = load_raw_pids(prof).get("ecus") or {}
    try:
        result = resolve(
            ecus, _selected_variant_def(prof), prof.revision_pins, prof.revision_detections
        )
    except RevisionError as e:
        raise ProfileError(str(e)) from None
    _resolution_cache[key] = result
    return result


def _resolved(prof) -> dict:
    """``prof``'s definitions for the selected car (see :func:`load_pids`)."""
    from canlib.variants.revisions import apply, has_gates, skipped_note
    from canlib.variants.tags import selected_variant

    raw = load_raw_pids(prof)
    ecus = raw.get("ecus") or {}
    if not has_gates(ecus) and not prof.revision_pins:
        result = dict(prof.meta)
        result["ecus"] = ecus
        return result
    resolved, skipped = apply(ecus, selected_variant(prof), revision_resolutions(prof))
    if skipped and prof.cache_key not in _noted_skips:
        _noted_skips.add(prof.cache_key)
        unchosen: list[str] = []
        if selected_variant(prof) is None:
            from canlib.variants import load_variants

            unchosen = list(load_variants(prof))
        print(skipped_note(prof.name, skipped, unchosen), file=sys.stderr)
    result = dict(prof.meta)
    result["ecus"] = resolved
    return result


def merge_ecu_documents(docs: Iterable[tuple[Path, dict]]) -> dict[str, Any]:
    """Merge per-file ECU documents into one ``{ECU_NAME: definition}`` mapping.

    One ECU per top-level key, one file per ECU by convention. A key claimed by
    two files is a mistake (two definitions for one ECU, only one of which the
    tool would ever see), so it warns and keeps the first — reporting the
    collision rather than silently letting file order decide. ``canair validate
    pids`` errors on it.

    A document whose top level is not a mapping (an empty file, a stray scalar) is
    skipped with a warning for the same reason: every reader of ``ecus/`` goes
    through here, so raising would turn one malformed file into an opaque crash in
    whatever command happened to touch it. Fields a PID states once for its parameters
    (:data:`INHERITED_PARAM_FIELDS`) are resolved into each parameter here, so no reader
    has to know about the inheritance. ``canair validate pids`` is the gate
    that reports it as an error.
    """
    merged: dict[str, Any] = {}
    origin: dict[str, Path] = {}
    for path, data in docs:
        if data is None:
            continue
        if not isinstance(data, dict):
            print(
                f"warning: {path.name} is not an ECU definition mapping "
                f"(top level is {type(data).__name__}); skipping. "
                f"Run `canair validate pids`.",
                file=sys.stderr,
            )
            continue
        for name, definition in data.items():
            if name in merged:
                print(
                    f"warning: ECU '{name}' is defined in both {origin[name].name} "
                    f"and {path.name}; using {origin[name].name}. "
                    f"Run `canair validate pids`.",
                    file=sys.stderr,
                )
                continue
            merged[name] = definition
            origin[name] = path
    _inherit_pid_fields(merged)
    return merged


def _load_dir(path: Path, meta: dict) -> dict:
    """Merge profile-wide meta with every per-ECU file under ``path``."""
    from canlib.ecus.files import iter_ecu_files

    def _docs():
        for fpath in iter_ecu_files(path):
            with open(fpath) as f:
                yield fpath, _yaml_load(f)

    result = dict(meta) if meta else {}
    result["ecus"] = merge_ecu_documents(_docs())
    return result


def build_param_index(pids_data: dict) -> dict:
    """Build lookup: PARAM_NAME -> {ecu, tx_id, pid, expression, unit, ...}."""
    index = {}
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        tx_id = ecu_def["tx_id"]
        for pid_code, pid_def in ecu_def.get("pids", {}).items():
            if pid_status(pid_def) == "ignored":
                continue
            for param_name, param in pid_def.get("parameters", {}).items():
                index[param_name.upper()] = {
                    "ecu": ecu_name,
                    "tx_id": tx_id,
                    "pid": str(pid_code),
                    "expression": param.get("expression", ""),
                    "unit": param.get("unit", ""),
                    "verified": param.get("verified", False),
                    "ha_class": param.get("ha_class", ""),
                    "display": param.get("display", ""),
                    "type": param.get("type", ""),
                    "values": param.get("values", {}),
                    "bits": param.get("bits", {}),
                    "fields": param.get("fields", []),
                }
    return index


# UDS extendedDiagnosticSession — what an actuator opens unless it says otherwise.
DEFAULT_IOCONTROL_SESSION_MODE = "03"


def normalize_session_mode(value: object) -> str:
    """A declared ``session_mode`` as two upper-case hex digits (default ``03``).

    Accepts only one hex byte as a *string* (an optional ``0x`` prefix is
    tolerated). Anything else raises :class:`ValueError` — notably an unquoted
    YAML ``session_mode: 0xC0``, which loads as the integer 192 and would
    otherwise be sent as the nonsense request ``10192``.
    """
    if value is None:
        return DEFAULT_IOCONTROL_SESSION_MODE
    if isinstance(value, bool) or not isinstance(value, str):
        hint = " — quote it so YAML does not read it as a number" if isinstance(value, int) else ""
        raise ValueError(
            f'session_mode must be one quoted hex byte (e.g. "03" or "C0"), got {value!r}{hint}'
        )
    text = value.strip().upper().removeprefix("0X")
    if not text:
        return DEFAULT_IOCONTROL_SESSION_MODE
    if len(text) != 2 or any(c not in "0123456789ABCDEF" for c in text):
        raise ValueError(
            f'session_mode must be one hex byte (two digits, e.g. "03" or "C0"), got {value!r}'
        )
    return text


def build_iocontrol_index(
    pids_data: dict, include_discoveries: bool = False
) -> dict[str, IoControlIndexEntry]:
    """Build lookup: ECU_NAME -> {tx_id, cmds: {DID: IoControlCommand}}.

    When ``include_discoveries=True``, entries from the ``iocontrol_discoveries:``
    section are merged in with ``discovery=True``. Curated ``iocontrol:`` entries
    take precedence if a DID appears in both. Discovery entries get safe defaults
    (label="?", on="", off="", verified=False, session=True, discovery=True).
    """
    index: dict[str, IoControlIndexEntry] = {}
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        ioctrl = ecu_def.get("iocontrol", {})
        discoveries = ecu_def.get("iocontrol_discoveries", {}) if include_discoveries else {}
        if not ioctrl and not discoveries:
            continue
        identity = ecu_def.get("identity")
        protocol = session_protocol(
            identity.get("id_protocol") if isinstance(identity, dict) else None
        )
        cmds: dict[str, IoControlCommand] = {}
        for did, cdef in ioctrl.items():
            did_str = str(did).upper()
            try:
                session_mode = normalize_session_mode(cdef.get("session_mode"))
                # YAML parses bare on/off as True/False booleans
                on_cmd = str(cdef.get("on") or cdef.get(True, ""))
                off_cmd = str(cdef.get("off") or cdef.get(False, ""))
                release = _resolve_release(cdef.get("release"), on_cmd, off_cmd, protocol, did_str)
            except ValueError as exc:
                from .profile import ProfileError

                raise ProfileError(f"{ecu_name}/iocontrol/{did_str}: {exc}") from None
            cmds[did_str] = {
                "label": cdef.get("label", ""),
                "on": on_cmd,
                "off": off_cmd,
                "release": release,
                "session": cdef.get("session", True),
                "session_mode": session_mode,
                "hold": cdef.get("hold", True),
                "verified": cdef.get("verified", False),
                "notes": cdef.get("notes", ""),
                "status_param": cdef.get("status_param", None),
                "discovery": False,
            }
        for did, ddef in discoveries.items():
            did_str = str(did).upper()
            if did_str in cmds:
                # Curated entry wins; discovery is shadowed.
                continue
            ddef = ddef or {}
            cmds[did_str] = {
                "label": "?",
                "on": "",
                "off": "",
                # A 4-digit key is a UDS DID, whose release is implied; a 2-digit
                # KWP2000 LID has none.
                "release": _resolve_release(None, "", "", protocol, did_str),
                "session": (ddef.get("session", "extended") == "extended"),
                "session_mode": DEFAULT_IOCONTROL_SESSION_MODE,
                "hold": True,
                "verified": False,
                "notes": ddef.get("notes", ""),
                "status_param": None,
                "discovery": True,
            }
        index[ecu_name.upper()] = {
            "tx_id": ecu_def["tx_id"],
            "protocol": protocol,
            "cmds": cmds,
        }
    return index


def _resolve_release(
    declared: object, on: str, off: str, protocol: SessionProtocol, key: str = ""
) -> str:
    """An entry's release: the declared ``release:``, else the one it implies.

    ``on``/``off`` imply one when they are UDS ``0x2F`` requests. An entry with
    neither (driven only by the value keys, which build ``2F {key} 03 …``) implies
    the release of its own 4-digit DID key.
    """
    if declared not in (None, ""):
        if not isinstance(declared, str):
            raise ValueError(f"release must be a quoted hex request, got {declared!r}")
        return normalize_request(declared)
    if not on and not off and len(key) == 4 and all(c in "0123456789ABCDEF" for c in key):
        on = f"2F{key}00"
    for request in (on, off):
        if request:
            implied = default_release(request, protocol)
            if implied:
                return implied
    return ""


def build_routines_index(pids_data: dict) -> dict[str, RoutineIndexEntry]:
    """Build lookup: ECU_NAME -> {tx_id, routines: {RID: {label, nrc, nrc_desc, response, verified, notes}}}.

    Reads the ``routines:`` section from each ECU's YAML. Each entry corresponds
    to a RoutineControl (0x31) hit found by ``canair scan routines``. The TUI uses this
    to send sub-function 0x03 (requestRoutineResults — safe, read-only) and
    optionally 0x01 (startRoutine — only with explicit user confirmation).
    """
    index: dict[str, RoutineIndexEntry] = {}
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        routines = ecu_def.get("routines", {})
        if not routines:
            continue
        rmap = {}
        for rid, rdef in routines.items():
            rid_str = str(rid).upper()
            rdef = rdef or {}
            rmap[rid_str] = {
                "label": rdef.get("label", ""),
                "nrc": rdef.get("nrc"),
                "nrc_desc": rdef.get("nrc_desc", ""),
                "response": rdef.get("response", ""),
                "verified": rdef.get("verified", False),
                "notes": rdef.get("notes", ""),
            }
        index[ecu_name.upper()] = {
            "tx_id": ecu_def["tx_id"],
            "routines": rmap,
        }
    return index


def build_sessions_index(pids_data: dict) -> dict[str, SessionIndexEntry]:
    """Build lookup: ECU_NAME -> {tx_id, sessions: {MODE_HEX: {name, supported, ...}}}.

    Reads the ``sessions:`` section from each ECU's YAML, written by
    ``canair scan sessions`` (see :mod:`canlib.modes.sessions_scan`). Keys are
    2-hex-digit DiagnosticSessionControl (0x10) sub-functions, e.g. "03"/"81".
    Used to pre-populate a session-type picker with an ECU's *known-good*
    session types instead of a blind fixed list.
    """
    index: dict[str, SessionIndexEntry] = {}
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        sessions = ecu_def.get("sessions", {})
        if not sessions:
            continue
        smap = {}
        for mode, sdef in sessions.items():
            mode_str = str(mode).upper().zfill(2)
            sdef = sdef or {}
            smap[mode_str] = {
                "name": sdef.get("name", ""),
                "supported": bool(sdef.get("supported", False)),
                "nrc": sdef.get("nrc"),
                "nrc_desc": sdef.get("nrc_desc", ""),
            }
        index[ecu_name.upper()] = {
            "tx_id": ecu_def["tx_id"],
            "sessions": smap,
        }
    return index


def build_ecu_index(pids_data: dict) -> dict[str, EcuIndexEntry]:
    """Build lookup: ECU_NAME -> {tx_id, rx_id, pids: {PID: {parameters: ...}}}."""
    from .addressing import resolve_ecu_address
    from .modes.multi_batch import resolve_multi_did_max
    from .wake import resolve_wake

    index: dict[str, EcuIndexEntry] = {}
    default_batch = bool(pids_data.get("multi_did_batching", False))
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        tx_id = ecu_def["tx_id"]
        address = resolve_ecu_address(pids_data, ecu_def)
        entry: EcuIndexEntry = {
            "tx_id": tx_id,
            # Resolved CAN response address (explicit rx_id → profile offset →
            # default +8), so the raw transport doesn't recompute tx+8.
            "rx_id": address.rx_id,
            # Addressing mode (per-ECU → profile → 11-bit): the raw transport
            # builds the ISO-TP stack for this ECU from it.
            "mode": address.mode,
            # Full resolved addressing bundle (mode + RX + extended/FC bytes).
            "address": address,
            "pids": {},
            # UDS service-22 multi-DID batching: per-ECU flag, defaulting to the
            # profile-wide setting. Only ECUs that opt in are batched (and even
            # then it auto-falls back if the ECU rejects a multi-DID request).
            "multi_did": bool(ecu_def.get("multi_did", default_batch)),
            # Max DIDs combined per multi-DID request (per-ECU → profile → default).
            "multi_did_max": resolve_multi_did_max(pids_data, ecu_def),
            # Profile-declared wake ritual for a fast-sleeping ECU (or None).
            "wake": resolve_wake(ecu_def),
        }
        index[ecu_name.upper()] = entry
        for pid_code, pid_def in ecu_def.get("pids", {}).items():
            status = pid_status(pid_def)
            if status == "ignored":
                continue
            entry["pids"][str(pid_code).upper()] = {
                "parameters": pid_def.get("parameters", {}),
                "period": pid_def.get("period"),
                "status": status,
                # Derived visibility flags (single source: `status`) so callers
                # never re-implement the lifecycle rules:
                "shipped": is_shipped(status),
                "swept": is_swept(status),
                "support_map": pid_def.get("support_map"),
            }
    return index
