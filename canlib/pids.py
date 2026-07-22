"""YAML PID data loading and index building."""

from pathlib import Path

try:
    import yaml
except ImportError as e:
    raise ImportError("PyYAML not installed. Run: pip3 install pyyaml") from e


def load_pids(path: Path | None = None) -> dict:
    """Load PID definitions from YAML.

    Accepts either a directory (pids/) containing per-ECU YAML files,
    or a single YAML file (legacy ioniq-2017-pids.yaml format). When ``path``
    is None, the active vehicle profile's pids/ directory is used.
    """
    if path is None:
        from .profile import active

        path = active().pids_dir
    path = Path(path)

    if path.is_dir():
        # Load _meta.yaml for car_model/init
        meta_path = path / "_meta.yaml"
        if meta_path.exists():
            with open(meta_path) as f:
                result = yaml.safe_load(f) or {}
        else:
            result = {}
        result["ecus"] = {}

        # Load per-ECU files (all .yaml except _meta, _schema)
        for fpath in sorted(path.glob("*.yaml")):
            if fpath.name.startswith("_"):
                continue
            with open(fpath) as f:
                data = yaml.safe_load(f)
            if data:
                result["ecus"].update(data)
        return result

    # Legacy: single file
    with open(path) as f:
        return yaml.safe_load(f)


def build_param_index(pids_data: dict) -> dict:
    """Build lookup: PARAM_NAME -> {ecu, tx_id, pid, expression, unit, ...}."""
    index = {}
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        tx_id = ecu_def["tx_id"]
        for pid_code, pid_def in ecu_def.get("pids", {}).items():
            if pid_def.get("ignored", False):
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
            if pid_def.get("ignored", False):
                continue
            index[ecu_name.upper()]["pids"][str(pid_code).upper()] = {
                "parameters": pid_def.get("parameters", {}),
                "period": pid_def.get("period", 5000),
                "enabled": pid_def.get("enabled", True),
            }
    return index
