"""The signal-level diff of two grouped AutoPID profiles."""

from __future__ import annotations

from canair.interop.autopid.diff import diff_profiles


def _profile(*pids):
    return {
        "pids": [{"pid_init": "i", "pid": p, "parameters": s, "send_to": t} for p, s, t in pids]
    }


def test_identical_profiles_do_not_differ():
    profile = _profile(("2101", {"SOC": "B9/2"}, {}))
    diff = diff_profiles(profile, profile)
    assert not diff.has_diff and diff.pids == []


def test_a_pid_on_one_side_lists_all_its_signals():
    cur = _profile(("2101", {"A": "B1", "B": "B2"}, {}))
    gen = _profile(("2102", {"C": "B3"}, {}))
    diff = diff_profiles(cur, gen)
    assert [(p.pid, p.kind, len(p.signals)) for p in diff.pids] == [
        ("2101", "removed", 2),
        ("2102", "added", 1),
    ]
    assert (diff.added_pids, diff.removed_pids) == (1, 1)
    assert (diff.added_signals, diff.removed_signals, diff.changed_signals) == (1, 2, 0)


def test_expression_and_topic_changes_count_once_per_signal():
    cur = _profile(("2101", {"A": "B1", "B": "B2"}, {"A": "t/a"}))
    gen = _profile(("2101", {"A": "B9", "C": "B3"}, {"A": "t/b"}))
    (pid,) = diff_profiles(cur, gen).pids
    assert [(s.name, s.change) for s in pid.signals] == [
        ("A", "changed"),
        ("B", "removed"),
        ("C", "added"),
    ]
    assert pid.signals[0].old == ("B1", "t/a") and pid.signals[0].new == ("B9", "t/b")
    diff = diff_profiles(cur, gen)
    assert (diff.added_signals, diff.removed_signals, diff.changed_signals) == (1, 1, 1)
