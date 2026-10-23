"""Choosing what to monitor when ``canair monitor`` is run with no selectors.

Pure, Textual-free logic: turn the profile's ECU index (and saved ``@groups``) into
pickable choices, and turn the user's picks back into the ordinary selector strings
(``BMS``, ``VCU:2101,2102``) the multi mini-language already understands. The
interactive screen lives in :mod:`canlib.modes._monitor_select_tui`; keeping the
mapping here means it is unit-testable without a terminal and cannot drift from the
sweep rules in :func:`canlib.modes.multi_exec.build_query_plan`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from canlib.ecu_groups import Group


@dataclass(frozen=True)
class PidChoice:
    """One pollable PID under an ECU."""

    pid: str
    signals: str  # comma-joined decoded signal names ("" for an undecoded PID)
    status: str
    swept: bool  # included by a bare-ECU selector (see ``pids.is_swept``)


@dataclass(frozen=True)
class EcuChoice:
    """One ECU with at least one pollable PID."""

    name: str
    tx_id: int
    pids: tuple[PidChoice, ...]

    def default_pids(self, include_static: bool) -> list[str]:
        """The PIDs a bare ``canair monitor ECU`` would poll, in display order."""
        return [p.pid for p in self.pids if include_static or p.swept]


def build_choices(ecu_index: Mapping[str, Any]) -> list[EcuChoice]:
    """Every ECU in ``ecu_index`` that has something to poll, sorted by name.

    Identity-only modules (no non-ignored PIDs) are left out — selecting one
    would produce a monitor with nothing in it.
    """
    out: list[EcuChoice] = []
    for name in sorted(ecu_index):
        info = ecu_index[name]
        pids = tuple(
            PidChoice(
                pid=code,
                signals=", ".join((pdef.get("parameters") or {}).keys()),
                status=str(pdef.get("status", "active")),
                swept=bool(pdef.get("swept", True)),
            )
            for code, pdef in sorted(info["pids"].items())
        )
        if pids:
            out.append(EcuChoice(name=name, tx_id=info["tx_id"], pids=pids))
    return out


@dataclass(frozen=True)
class GroupChoice:
    """A saved ``@group`` resolved against the pickable ECUs and PIDs.

    ``picks`` maps each ECU to the PIDs the group polls (a bare member means that
    ECU's default sweep). ``skipped`` lists members the picker cannot represent —
    an ECU with nothing to poll or a PID the profile does not define — so the UI
    can say so rather than silently polling less than the group promises.
    """

    name: str
    description: str
    picks: dict[str, tuple[str, ...]]
    skipped: tuple[str, ...] = ()


def _resolve_members(
    members: Sequence[str], by_name: Mapping[str, EcuChoice], include_static: bool
) -> tuple[dict[str, tuple[str, ...]], list[str]]:
    from .multi_parse import parse_sub_commands

    try:
        steps = parse_sub_commands([f"query {m}" for m in members])
    except ValueError:
        return {}, list(members)

    picks: dict[str, tuple[str, ...]] = {}
    skipped: list[str] = []
    for step in steps:
        name = step["ecu"].upper()
        ecu = by_name.get(name)
        if ecu is None:
            skipped.append(name)
            continue
        wanted = [p.upper() for p in step.get("pids") or []]
        if not wanted:
            picked = set(ecu.default_pids(include_static))
        else:
            picked = set()
            for want in wanted:
                hits = [p.pid for p in ecu.pids if p.pid == want or p.pid.endswith(want)]
                if hits:
                    picked.update(hits)
                else:
                    skipped.append(f"{name}:{want}")
        if picked:
            picks[name] = tuple(p.pid for p in ecu.pids if p.pid in picked)
    return picks, skipped


def build_group_choices(
    groups: Mapping[str, Group], choices: Sequence[EcuChoice], *, include_static: bool = False
) -> list[GroupChoice]:
    """The saved groups that resolve to at least one pickable PID, in file order."""
    by_name = {c.name: c for c in choices}
    out: list[GroupChoice] = []
    for group in groups.values():
        picks, skipped = _resolve_members(group.members, by_name, include_static)
        if picks:
            out.append(GroupChoice(group.name, group.description, picks, tuple(skipped)))
    return out


def selectors_from_selection(
    choices: Sequence[EcuChoice],
    selected: Mapping[str, Sequence[str]],
    *,
    include_static: bool = False,
) -> list[str]:
    """Selector strings for the user's picks, one per ECU, in ``choices`` order.

    An ECU whose picks are exactly its default sweep collapses to the bare name,
    so the command printed back to the user is the short one they could have
    typed (and a PID later added to that ECU is picked up like any other sweep).
    Anything else is spelled out as ``ECU:PID,PID`` — an explicit PID is always
    polled, even one a bare sweep would skip.
    """
    out: list[str] = []
    for ecu in choices:
        picked = set(selected.get(ecu.name, ()))
        if not picked:
            continue
        sweep = set(ecu.default_pids(include_static))
        if sweep and picked == sweep:
            out.append(ecu.name)
        else:
            out.append(f"{ecu.name}:{','.join(p.pid for p in ecu.pids if p.pid in picked)}")
    return out


def pick_selectors(pids_data: dict, *, include_static: bool = False) -> list[str] | None:
    """Run the interactive picker; return selectors, or ``None`` if cancelled.

    Raises ``ValueError`` when the active profile defines nothing to monitor.
    The caller is responsible for only calling this on an interactive terminal.
    """
    from ..ecu_groups import load_groups
    from ..pids import build_ecu_index

    choices = build_choices(build_ecu_index(pids_data))
    if not choices:
        raise ValueError("the active profile defines no pollable PIDs, so there is nothing to pick")

    from ._monitor_select_tui import MonitorSelectApp

    groups = build_group_choices(load_groups(), choices, include_static=include_static)
    picked = MonitorSelectApp(choices, groups, include_static=include_static).run()
    if not picked:
        return None
    return selectors_from_selection(choices, picked, include_static=include_static)
