"""Shell-completion callbacks (argcomplete) for the live subcommands' arguments."""

from __future__ import annotations

from canlib import (
    load_pids,
)


def _load_pids_for_completion():
    try:
        return load_pids()
    except Exception:
        return None


def ecu_completer(prefix, parsed_args=None, **kwargs):
    """Complete ECU names from ecus/*.yaml (e.g. BMS, IGPM, HVAC)."""
    data = _load_pids_for_completion()
    if not data:
        return []
    names = list(data.get("ecus", {}).keys())
    up = prefix.upper()
    return [n for n in names if n.upper().startswith(up)]


def pid_completer(prefix, parsed_args=None, **kwargs):
    """Complete PID codes. Narrows to --ecu's PIDs if that arg is set."""
    data = _load_pids_for_completion()
    if not data:
        return []
    ecus = data.get("ecus", {})
    ecu_filter = getattr(parsed_args, "ecu", None)
    pids = set()
    if ecu_filter and ecu_filter.upper() in {k.upper() for k in ecus}:
        target = next(k for k in ecus if k.upper() == ecu_filter.upper())
        pids.update(ecus[target].get("pids", {}).keys())
    else:
        for info in ecus.values():
            pids.update(info.get("pids", {}).keys())
    codes = [str(p).upper().removeprefix("0X") for p in pids]
    up = prefix.upper()
    return sorted(c for c in codes if c.startswith(up))


def param_completer(prefix, parsed_args=None, **kwargs):
    """Complete parameter names from all ECUs' pids."""
    data = _load_pids_for_completion()
    if not data:
        return []
    names = set()
    for info in data.get("ecus", {}).values():
        for pid_info in info.get("pids", {}).values():
            if not isinstance(pid_info, dict):
                continue
            for param in pid_info.get("params", []) or []:
                if isinstance(param, dict) and "name" in param:
                    names.add(param["name"])
    up = prefix.upper()
    return sorted(n for n in names if n.upper().startswith(up))


def step_completer(prefix, parsed_args=None, **kwargs):
    """Complete a query STEP: ``@group`` refs when it starts with ``@``, else ECUs."""
    if prefix.startswith("@"):
        try:
            from canlib.ecu_groups import GROUP_SIGIL, load_groups

            names = [f"{GROUP_SIGIL}{g}" for g in load_groups()]
        except Exception:
            return []
        return [n for n in names if n.startswith(prefix)]
    return ecu_completer(prefix, parsed_args, **kwargs)
