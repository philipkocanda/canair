"""``canair wican autopid diff``: a signal-level diff of the device against ours."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from canair.interop.autopid.profile import (
    normalize_device_profile,
)
from canair.render import messages

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


def _describe(signal: Signal) -> str:
    expr, topic = signal
    return f"{expr}  → {topic}" if topic else expr


def show_diff(current_raw: dict | None, generated: dict) -> bool:
    """Show signal-level diff between device and generated profile.

    Normalizes the device's flat format to grouped format for comparison.
    Returns True if there are differences.
    """
    if current_raw is None:
        messages.error("no current config to compare (download failed)")
        return True

    diff = diff_profiles(normalize_device_profile(current_raw), generated)

    for pid in diff.pids:
        if pid.kind == "added":
            print(f"\n  + NEW PID: {pid.pid_init} {pid.pid} ({len(pid.signals)} signals)")
            for s in pid.signals:
                assert s.new is not None
                print(f"      + {s.name}: {_describe(s.new)}")
        elif pid.kind == "removed":
            print(f"\n  - REMOVED PID: {pid.pid_init} {pid.pid} ({len(pid.signals)} signals)")
            for s in pid.signals:
                assert s.old is not None
                print(f"      - {s.name}: {_describe(s.old)}")
        else:
            print(f"\n  ~ CHANGED: {pid.pid_init} {pid.pid}")
            for s in pid.signals:
                if s.change == "added":
                    assert s.new is not None
                    print(f"      + {s.name}: {_describe(s.new)}")
                elif s.change == "removed":
                    assert s.old is not None
                    print(f"      - {s.name}: {_describe(s.old)}")
                else:
                    assert s.old is not None and s.new is not None
                    (cur_expr, cur_topic), (gen_expr, gen_topic) = s.old, s.new
                    if cur_expr != gen_expr:
                        print(f"      ~ {s.name}: {cur_expr} → {gen_expr}")
                    if cur_topic != gen_topic:
                        print(
                            f"      ~ {s.name}: send_to {cur_topic or '(none)'} → "
                            f"{gen_topic or '(none)'}"
                        )

    if not diff.has_diff:
        print("\n  No differences between device and generated profile")
    else:
        print(
            f"\n  Summary: +{diff.added_pids} PIDs, -{diff.removed_pids} PIDs, "
            f"+{diff.added_signals} signals, -{diff.removed_signals} signals, "
            f"~{diff.changed_signals} changed"
        )

    return diff.has_diff
