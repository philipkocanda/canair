"""Comparing two AutoPID profiles signal by signal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Signal = tuple[str, str]  # (expression, send_to topic or "")


@dataclass(frozen=True)
class SignalDiff:
    """One signal of a PID that differs: ``old`` is the device's, ``new`` is ours."""

    name: str
    change: Literal["added", "removed", "changed"]
    old: Signal | None
    new: Signal | None


@dataclass(frozen=True)
class PidDiff:
    """One PID that differs. A PID on one side only lists all its signals."""

    pid_init: str
    pid: str
    kind: Literal["added", "removed", "changed"]
    signals: list[SignalDiff] = field(default_factory=list)


@dataclass(frozen=True)
class ProfileDiff:
    pids: list[PidDiff]
    added_pids: int
    removed_pids: int
    added_signals: int
    removed_signals: int
    changed_signals: int

    @property
    def has_diff(self) -> bool:
        return bool(self.pids)


def _signal_map(profile: dict) -> dict[tuple[str, str], dict[str, Signal]]:
    """``(pid_init, pid) -> {signal: (expression, send_to topic or "")}`` for diffing."""
    result = {}
    for entry in profile["pids"]:
        send_to = entry.get("send_to") or {}
        result[(entry["pid_init"], entry["pid"])] = {
            name: (expr, send_to.get(name, "")) for name, expr in entry["parameters"].items()
        }
    return result


def diff_profiles(current: dict, generated: dict) -> ProfileDiff:
    """Compare two grouped profiles signal by signal.

    A signal differs if its expression or its ``send_to`` stream topic differs.
    """
    cur_map = _signal_map(current)
    gen_map = _signal_map(generated)
    pids: list[PidDiff] = []
    added_pids = removed_pids = added = removed = changed = 0

    for key in sorted(set(cur_map) | set(gen_map)):
        pid_init, pid = key
        cur = cur_map.get(key, {})
        gen = gen_map.get(key, {})

        if not cur and gen:
            added_pids += 1
            added += len(gen)
            pids.append(
                PidDiff(pid_init, pid, "added", [SignalDiff(n, "added", None, gen[n]) for n in gen])
            )
            continue
        if cur and not gen:
            removed_pids += 1
            removed += len(cur)
            pids.append(
                PidDiff(
                    pid_init, pid, "removed", [SignalDiff(n, "removed", cur[n], None) for n in cur]
                )
            )
            continue

        signals: list[SignalDiff] = []
        for name in sorted(set(cur) | set(gen)):
            if name not in cur:
                signals.append(SignalDiff(name, "added", None, gen[name]))
                added += 1
            elif name not in gen:
                signals.append(SignalDiff(name, "removed", cur[name], None))
                removed += 1
            elif cur[name] != gen[name]:
                signals.append(SignalDiff(name, "changed", cur[name], gen[name]))
                changed += 1
        if signals:
            pids.append(PidDiff(pid_init, pid, "changed", signals))

    return ProfileDiff(pids, added_pids, removed_pids, added, removed, changed)
