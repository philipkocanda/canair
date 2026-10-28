"""send_to validation — the per-signal MQTT stream topic (see ``canlib.wican.autopid_send_to``).

Checks every signal that sets ``send_to`` across the given ecus/ files: the topic must be
well-formed (an error), unique across shipped signals (an error, since two signals on one topic
interleave on a single stream), and set only where it would actually ship (a warning otherwise).
Mirrors the shipping gate of ``canlib.wican.autopid_profile.generate_profile``.
"""

from pathlib import Path

import yaml

from canlib import yaml_io
from canlib.pids import is_shipped, pid_status
from canlib.wican.autopid_send_to import topic_problem


def _load(fpath: Path) -> dict:
    try:
        with open(fpath) as f:
            data = yaml_io.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}  # validate_ecu_file reports unreadable files
    return data if isinstance(data, dict) else {}


def _never_ships(pid_def: dict, param: dict) -> str | None:
    """Why this signal is excluded from every AutoPID build, or ``None``."""
    status = pid_status(pid_def)
    if not is_shipped(status):
        return f"its PID is '{status}'"
    if not param.get("enabled", True):
        return "the signal is disabled"
    return None


def send_to_findings(file_paths: list[Path]) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for every ``send_to`` in ``file_paths``."""
    errors: list[str] = []
    warnings: list[str] = []
    owner: dict[str, str] = {}
    for fpath in file_paths:
        if fpath.name.startswith("_"):
            continue
        for ecu_name, ecu_def in _load(fpath).items():
            if not isinstance(ecu_def, dict):
                continue
            for pid_code, pid_def in (ecu_def.get("pids") or {}).items():
                if not isinstance(pid_def, dict):
                    continue
                for pname, param in (pid_def.get("parameters") or {}).items():
                    if not isinstance(param, dict) or "send_to" not in param:
                        continue
                    label = f"{ecu_name}/{pid_code}/{pname}"
                    topic = param["send_to"]
                    problem = topic_problem(topic)
                    if problem:
                        errors.append(f"{label}: send_to {problem}")
                        continue
                    excluded = _never_ships(pid_def, param)
                    if excluded:
                        warnings.append(
                            f"{label}: send_to '{topic}' has no effect — {excluded}, "
                            "so it never ships"
                        )
                        continue
                    if not param.get("verified", False):
                        # Still checked for uniqueness: --include-unverified ships it.
                        warnings.append(
                            f"{label}: send_to '{topic}' ships only with --include-unverified "
                            "(the signal is unverified)"
                        )
                    if topic in owner:
                        errors.append(
                            f"{label}: send_to '{topic}' is also used by {owner[topic]} — "
                            "each shipped signal needs its own topic"
                        )
                    else:
                        owner[topic] = label
    return errors, warnings
