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
from canlib.addressing import AddressingMode, EcuAddress

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
PidStatus = Literal["active", "draft", "static", "ignored"]
PID_STATUSES: tuple[PidStatus, ...] = get_args(PidStatus)
DEFAULT_PID_STATUS: PidStatus = "active"


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


class PidIndexEntry(TypedDict):
    """One PID's entry in a :func:`build_ecu_index` result.

    ``shipped``/``swept`` are derived from ``status`` (single source of truth) so
    callers never re-implement the lifecycle rules — see ``PID_STATUSES``.
    """

    parameters: dict[str, Any]
    period: int
    status: PidStatus
    shipped: bool  # include in the generated WiCAN device profile (status == active)
    swept: bool  # include in a bare-ECU sweep (status != static)


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

    ``on``/``off`` are the full UDS ``0x2F`` request hex strings; either may be
    ``""`` when this DID has no simple command for that direction (common for
    ShortTermAdjustment actuators needing value bytes, and for every
    scanner-discovered entry). Callers must treat an empty string as "refuse",
    never as a reason to fall through to the other direction.

    ``discovery`` marks an entry merged in from ``iocontrol_discoveries:``
    (probed, not curated) rather than the hand-verified ``iocontrol:`` section.
    """

    label: str
    on: str
    off: str
    session: bool
    hold: bool
    verified: bool
    notes: str
    status_param: str | None
    discovery: bool


class IoControlIndexEntry(TypedDict):
    """One ECU's entry in a :func:`build_iocontrol_index` result."""

    tx_id: int
    cmds: dict[str, IoControlCommand]


class RoutineIndexEntry(TypedDict):
    """One ECU's entry in a :func:`build_routines_index` result."""

    tx_id: int
    routines: dict[str, dict[str, Any]]


def _yaml_load(fh) -> dict:
    return yaml_io.safe_load(fh)


# ── per-process memoization ───────────────────────────────────────────────
# The active profile's ECU dir is parsed once per process and reused. Writers
# (canlib.pids_edit / canlib.ecus_edit) call clear_cache() after mutating a
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
    for clear in _derived_cache_hooks:
        clear()


def load_pids(path: Path | None = None) -> dict:
    """Load ECU/PID definitions from a profile's ``ecus/`` directory.

    Accepts either a directory (``ecus/``) containing per-ECU YAML files, or a
    single legacy YAML file. When ``path`` is None, the active vehicle profile's
    ``ecus/`` directory is used (and the result is memoized per process).
    """
    if path is None:
        from .profile import active

        prof = active()
        key = prof.cache_key
        cached = _cache.get(key)
        if cached is not None:
            return cached
        result = _load_dir(prof.ecus_dir, meta=prof.meta)
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
    whatever command happened to touch it. ``canair validate pids`` is the gate
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
    return merged


def _load_dir(path: Path, meta: dict) -> dict:
    """Merge profile-wide meta with every per-ECU file under ``path``."""
    from .ecu_files import iter_ecu_files

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


def build_iocontrol_index(
    pids_data: dict, include_discoveries: bool = False
) -> dict[str, IoControlIndexEntry]:
    """Build lookup: ECU_NAME -> {tx_id, cmds: {DID: {label, on, off, session, hold, verified, notes, discovery}}}.

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
        cmds: dict[str, IoControlCommand] = {}
        for did, cdef in ioctrl.items():
            did_str = str(did).upper()
            # YAML parses bare on/off as True/False booleans
            on_cmd = cdef.get("on") or cdef.get(True, "")
            off_cmd = cdef.get("off") or cdef.get(False, "")
            cmds[did_str] = {
                "label": cdef.get("label", ""),
                "on": str(on_cmd),
                "off": str(off_cmd),
                "session": cdef.get("session", True),
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
                "session": (ddef.get("session", "extended") == "extended"),
                "hold": True,
                "verified": False,
                "notes": ddef.get("notes", ""),
                "status_param": None,
                "discovery": True,
            }
        index[ecu_name.upper()] = {
            "tx_id": ecu_def["tx_id"],
            "cmds": cmds,
        }
    return index


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
                "period": pid_def.get("period", 5000),
                "status": status,
                # Derived visibility flags (single source: `status`) so callers
                # never re-implement the lifecycle rules:
                "shipped": status == "active",  # include in generated WiCAN profile
                "swept": status != "static",  # include in a bare-ECU sweep
            }
    return index
