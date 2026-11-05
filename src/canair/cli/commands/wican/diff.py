"""``canair wican autopid diff``: a signal-level diff of the device against ours."""

from __future__ import annotations

from canair.interop.autopid.diff import Signal, diff_profiles
from canair.interop.autopid.profile import normalize_device_profile
from canair.render import messages


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
