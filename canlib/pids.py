"""YAML PID/ECU data loading and index building.

Each vehicle profile stores its ECU definitions as one YAML file per ECU under
``<profile>/ecus/``. Every file is the single source of truth for one ECU:
identity, probe history (``scan_log``), DTC meanings (``dtcs``), and all
readable/actuatable data (``pids``/``iocontrol``/``routines``). Profile-wide
settings (``car_model``, ``init``, ``failure_types``, ...) live one level up in
``<profile>/profile.yaml``.
"""

from pathlib import Path

try:
    import yaml
except ImportError as e:
    raise ImportError("PyYAML not installed. Run: pip3 install pyyaml") from e

# Prefer the libyaml-backed C loader when available (3-10x faster parse, no
# behavioural difference). Falls back to the pure-Python SafeLoader otherwise.
_SafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

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
PID_STATUSES = ("active", "draft", "static", "ignored")
DEFAULT_PID_STATUS = "active"


def pid_status(pid_def: dict) -> str:
    """Return a PID definition's lifecycle status, defaulting to ``active``.

    Tolerant of unknown/missing values (treated as ``active``) so a malformed
    file degrades to "visible" rather than silently disappearing.
    """
    status = str((pid_def or {}).get("status", DEFAULT_PID_STATUS)).strip().lower()
    return status if status in PID_STATUSES else DEFAULT_PID_STATUS


def _yaml_load(fh) -> dict:
    return yaml.load(fh, Loader=_SafeLoader)


# ── per-process memoization ───────────────────────────────────────────────
# The active profile's ECU dir is parsed once per process and reused. Writers
# (canlib.pids_edit / canlib.ecus_edit) call clear_cache() after mutating a
# file so a later read in the same process never sees stale data.
_cache: dict[str, dict] = {}


def clear_cache() -> None:
    """Drop the memoized ECU-definition load (call after writing any ECU file)."""
    _cache.clear()


def load_pids(path: Path | None = None) -> dict:
    """Load ECU/PID definitions from a profile's ``ecus/`` directory.

    Accepts either a directory (``ecus/``) containing per-ECU YAML files, or a
    single legacy YAML file. When ``path`` is None, the active vehicle profile's
    ``ecus/`` directory is used (and the result is memoized per process).
    """
    if path is None:
        from .profile import active

        prof = active()
        key = str(prof.ecus_dir)
        cached = _cache.get(key)
        if cached is not None:
            return cached
        result = _load_dir(prof.ecus_dir, meta=prof.meta)
        _cache[key] = result
        return result

    path = Path(path)
    if path.is_dir():
        meta_path = path.parent / "profile.yaml"
        meta = {}
        if meta_path.exists():
            with open(meta_path) as f:
                meta = _yaml_load(f) or {}
        return _load_dir(path, meta=meta)

    # Legacy: single file
    with open(path) as f:
        return _yaml_load(f)


def _load_dir(path: Path, meta: dict) -> dict:
    """Merge profile-wide meta with every per-ECU file under ``path``."""
    result = dict(meta) if meta else {}
    result["ecus"] = {}
    for fpath in sorted(path.glob("*.yaml")):
        if fpath.name.startswith("_"):
            continue
        with open(fpath) as f:
            data = _yaml_load(f)
        if data:
            result["ecus"].update(data)
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
                }
    return index


def build_iocontrol_index(pids_data: dict, include_discoveries: bool = False) -> dict:
    """Build lookup: ECU_NAME -> {tx_id, cmds: {DID: {label, on, off, session, hold, verified, notes, discovery}}}.

    When ``include_discoveries=True``, entries from the ``iocontrol_discoveries:``
    section are merged in with ``discovery=True``. Curated ``iocontrol:`` entries
    take precedence if a DID appears in both. Discovery entries get safe defaults
    (label="?", on="", off="", verified=False, session=True, discovery=True).
    """
    index = {}
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        ioctrl = ecu_def.get("iocontrol", {})
        discoveries = ecu_def.get("iocontrol_discoveries", {}) if include_discoveries else {}
        if not ioctrl and not discoveries:
            continue
        cmds = {}
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


def build_routines_index(pids_data: dict) -> dict:
    """Build lookup: ECU_NAME -> {tx_id, routines: {RID: {label, nrc, nrc_desc, response, verified, notes}}}.

    Reads the ``routines:`` section from each ECU's YAML. Each entry corresponds
    to a RoutineControl (0x31) hit found by ``canair scan routines``. The TUI uses this
    to send sub-function 0x03 (requestRoutineResults — safe, read-only) and
    optionally 0x01 (startRoutine — only with explicit user confirmation).
    """
    index = {}
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


def build_ecu_index(pids_data: dict) -> dict:
    """Build lookup: ECU_NAME -> {tx_id, pids: {PID: {parameters: ...}}}."""
    index = {}
    default_batch = bool(pids_data.get("multi_did_batching", False))
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        index[ecu_name.upper()] = {
            "tx_id": ecu_def["tx_id"],
            "pids": {},
            # UDS service-22 multi-DID batching: per-ECU flag, defaulting to the
            # profile-wide setting. Only ECUs that opt in are batched (and even
            # then it auto-falls back if the ECU rejects a multi-DID request).
            "multi_did": bool(ecu_def.get("multi_did", default_batch)),
        }
        for pid_code, pid_def in ecu_def.get("pids", {}).items():
            status = pid_status(pid_def)
            if status == "ignored":
                continue
            index[ecu_name.upper()]["pids"][str(pid_code).upper()] = {
                "parameters": pid_def.get("parameters", {}),
                "period": pid_def.get("period", 5000),
                "status": status,
                # Derived visibility flags (single source: `status`) so callers
                # never re-implement the lifecycle rules:
                "shipped": status == "active",   # include in generated WiCAN profile
                "swept": status != "static",     # include in a bare-ECU sweep
            }
    return index
