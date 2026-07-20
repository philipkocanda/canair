"""Friendly "what next?" hints for subcommands: list ECUs / PIDs from the profile."""

from __future__ import annotations


def _pids_data() -> dict:
    try:
        from canlib.pids import load_pids

        return load_pids()
    except Exception:
        return {}


def list_ecus() -> list[str]:
    """Sorted ECU names defined in the active profile."""
    return sorted(_pids_data().get("ecus", {}).keys())


def _resolve_ecu_key(ecu: str, ecus: dict) -> str | None:
    return next((k for k in ecus if k.upper() == ecu.upper()), None)


def list_pids(ecu: str) -> list[str]:
    """PID codes defined for ``ecu`` (case-insensitive), uppercased."""
    ecus = _pids_data().get("ecus", {})
    key = _resolve_ecu_key(ecu, ecus)
    if key is None:
        return []
    return [str(p).upper() for p in (ecus[key].get("pids", {}) or {})]


def _columns(items: list[str], indent: str = "  ", width: int = 76) -> str:
    if not items:
        return f"{indent}(none)"
    pad = max(len(s) for s in items) + 2
    per_row = max(1, (width - len(indent)) // pad)
    lines = []
    for i in range(0, len(items), per_row):
        row = "".join(s.ljust(pad) for s in items[i : i + per_row])
        lines.append(indent + row.rstrip())
    return "\n".join(lines)


def ecu_hint() -> str:
    """A block listing the available ECUs in the active profile."""
    ecus = list_ecus()
    if not ecus:
        return "No ECUs found in the active profile (see `canair profile show`)."
    return "Available ECUs:\n" + _columns(ecus)


def pid_hint(ecu: str) -> str:
    """A block listing the PIDs for ``ecu`` (or an ECU list if ``ecu`` is unknown)."""
    ecus = _pids_data().get("ecus", {})
    key = _resolve_ecu_key(ecu, ecus)
    if key is None:
        return f"Unknown ECU {ecu!r}.\n" + ecu_hint()
    pids = list_pids(ecu)
    if not pids:
        return f"No PIDs defined for {key}."
    return f"Available PIDs for {key}:\n" + _columns(pids)


# ── argcomplete completers (lightweight — no live/websocket imports) ──────────


def ecu_completer(prefix, parsed_args=None, **kwargs):
    up = prefix.upper()
    return [e for e in list_ecus() if e.upper().startswith(up)]


def pid_completer(prefix, parsed_args=None, **kwargs):
    ecu = getattr(parsed_args, "ecu", None)
    data = _pids_data().get("ecus", {})
    codes: set[str] = set()
    if ecu and _resolve_ecu_key(ecu, data):
        codes.update(list_pids(ecu))
    else:
        for name in data:
            codes.update(str(p).upper() for p in (data[name].get("pids", {}) or {}))
    up = prefix.upper()
    return sorted(c for c in codes if c.startswith(up))
