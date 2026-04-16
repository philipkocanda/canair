"""YAML PID data loading and index building."""

from pathlib import Path

try:
    import yaml
except ImportError:
    raise ImportError("PyYAML not installed. Run: pip3 install pyyaml")

from .constants import ECUS_FILE, PIDS_FILE


def load_pids(path: Path = PIDS_FILE) -> dict:
    """Load PID definitions from YAML."""
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


def ecu_name(tx_id: int, ecus: dict = None) -> str:
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


def build_ecu_index(pids_data: dict) -> dict:
    """Build lookup: ECU_NAME -> {tx_id, pids: {PID: {parameters: ...}}}."""
    index = {}
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        index[ecu_name.upper()] = {
            "tx_id": ecu_def["tx_id"],
            "pids": {},
        }
        for pid_code, pid_def in ecu_def.get("pids", {}).items():
            index[ecu_name.upper()]["pids"][str(pid_code).upper()] = {
                "parameters": pid_def.get("parameters", {}),
                "period": pid_def.get("period", 5000),
                "enabled": pid_def.get("enabled", True),
            }
    return index
