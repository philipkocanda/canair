"""YAML PID data loading and index building."""

from pathlib import Path

try:
    import yaml
except ImportError as e:
    raise ImportError("PyYAML not installed. Run: pip3 install pyyaml") from e

from .constants import ECUS_FILE, PIDS_DIR


def load_pids(path: Path = PIDS_DIR) -> dict:
    """Load PID definitions from YAML.

    Accepts either a directory (pids/) containing per-ECU YAML files,
    or a single YAML file (legacy ioniq-2017-pids.yaml format).
    """
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


def load_ecus(path: Path = ECUS_FILE) -> dict:
    """Load ECU lookup table from YAML.

    Returns dict: tx_id (int) -> {name, description}.
    """
    with open(path) as f:
        data = yaml.safe_load(f)
    result = {}
    for tx_id, info in data.get("ecus", {}).items():
        if isinstance(tx_id, str) and tx_id.startswith("0x"):
            tx_id = int(tx_id, 16)
        result[int(tx_id)] = info
    return result


def ecu_name(tx_id: int, ecus: dict | None = None) -> str:
    """Get ECU name for a TX ID, or '0x{tx_id:03X}' if unknown."""
    if ecus is None:
        ecus = load_ecus()
    info = ecus.get(tx_id)
    return info["name"] if info else f"0x{tx_id:03X}"


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


def build_iocontrol_index(pids_data: dict) -> dict:
    """Build lookup: ECU_NAME -> {tx_id, cmds: {DID: {label, on, off, session, hold, verified, notes}}}."""
    index = {}
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        ioctrl = ecu_def.get("iocontrol", {})
        if not ioctrl:
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
            }
        index[ecu_name.upper()] = {
            "tx_id": ecu_def["tx_id"],
            "cmds": cmds,
        }
    return index


def build_ecu_index(pids_data: dict) -> dict:
    """Build lookup: ECU_NAME -> {tx_id, pids: {PID: {parameters: ...}}}."""
    index = {}
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        index[ecu_name.upper()] = {
            "tx_id": ecu_def["tx_id"],
            "pids": {},
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
