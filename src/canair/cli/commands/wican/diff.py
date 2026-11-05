"""``canair wican autopid diff``: a signal-level diff of the device against ours."""

from __future__ import annotations

from canair.interop.autopid.profile import (
    normalize_device_profile,
)
from canair.render import messages


def _signal_map(profile: dict) -> dict[tuple[str, str], dict[str, tuple[str, str]]]:
    """``(pid_init, pid) -> {signal: (expression, send_to topic or "")}`` for diffing."""
    result = {}
    for entry in profile["pids"]:
        send_to = entry.get("send_to") or {}
        result[(entry["pid_init"], entry["pid"])] = {
            name: (expr, send_to.get(name, "")) for name, expr in entry["parameters"].items()
        }
    return result


def _describe(signal: tuple[str, str]) -> str:
    expr, topic = signal
    return f"{expr}  → {topic}" if topic else expr


def show_diff(current_raw: dict | None, generated: dict) -> bool:
    """Show signal-level diff between device and generated profile.

    Normalizes the device's flat format to grouped format for comparison. A signal
    differs if its expression or its ``send_to`` stream topic differs.
    Returns True if there are differences.
    """
    if current_raw is None:
        messages.error("no current config to compare (download failed)")
        return True

    cur_map = _signal_map(normalize_device_profile(current_raw))
    gen_map = _signal_map(generated)

    all_keys = sorted(set(list(cur_map.keys()) + list(gen_map.keys())))

    has_diff = False
    added_params = 0
    removed_params = 0
    changed_params = 0
    added_pids = 0
    removed_pids = 0

    for key in all_keys:
        pid_init, pid = key
        cur_params = cur_map.get(key, {})
        gen_params = gen_map.get(key, {})

        if not cur_params and gen_params:
            added_pids += 1
            added_params += len(gen_params)
            has_diff = True
            print(f"\n  + NEW PID: {pid_init} {pid} ({len(gen_params)} signals)")
            for name in gen_params:
                print(f"      + {name}: {_describe(gen_params[name])}")
            continue

        if cur_params and not gen_params:
            removed_pids += 1
            removed_params += len(cur_params)
            has_diff = True
            print(f"\n  - REMOVED PID: {pid_init} {pid} ({len(cur_params)} signals)")
            for name in cur_params:
                print(f"      - {name}: {_describe(cur_params[name])}")
            continue

        # Both exist — compare signals
        all_param_names = sorted(set(list(cur_params.keys()) + list(gen_params.keys())))
        pid_diffs = []
        for name in all_param_names:
            if name not in cur_params:
                pid_diffs.append(f"      + {name}: {_describe(gen_params[name])}")
                added_params += 1
            elif name not in gen_params:
                pid_diffs.append(f"      - {name}: {_describe(cur_params[name])}")
                removed_params += 1
            elif cur_params[name] != gen_params[name]:
                (cur_expr, cur_topic), (gen_expr, gen_topic) = cur_params[name], gen_params[name]
                if cur_expr != gen_expr:
                    pid_diffs.append(f"      ~ {name}: {cur_expr} → {gen_expr}")
                if cur_topic != gen_topic:
                    pid_diffs.append(
                        f"      ~ {name}: send_to {cur_topic or '(none)'} → {gen_topic or '(none)'}"
                    )
                changed_params += 1

        if pid_diffs:
            has_diff = True
            print(f"\n  ~ CHANGED: {pid_init} {pid}")
            for d in pid_diffs:
                print(d)

    if not has_diff:
        print("\n  No differences between device and generated profile")
    else:
        print(
            f"\n  Summary: +{added_pids} PIDs, -{removed_pids} PIDs, "
            f"+{added_params} signals, -{removed_params} signals, ~{changed_params} changed"
        )

    return has_diff
