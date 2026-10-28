"""Bus-diagram layout for the docs explorer: which lane and column each ECU sits in.

The profile says which segment(s) an ECU is on (``can_bus:``) and nothing about the
physical wiring, so the result is an approximation. It is computed here, not in the
page's JavaScript, so it is deterministic and unit-testable; the page only draws it.

- One lane per declared segment, in declaration order. The gateway code (``ALL``) is not a
  lane: an ECU tagged with it is drawn as a gateway node spanning every lane except the
  ``private`` ones (links the gateway does not bridge, such as a climate-control bus).
- An ECU on several segments is one node with a tick on each lane (a bridge), at the same
  column on all of them.
- An ECU with no ``can_bus:`` goes in a trailing "unplaced" lane.
- When the ``ALL`` segment's description mentions the OBD-II port, an OBD-II node spans
  the same lanes (it explains the gateway behaviour the profile pages describe).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from canlib.can_buses import ALL_CODE

UNPLACED = "unplaced"


def _lanes(buses: Sequence[Mapping], ecus: Sequence[Mapping]) -> list[dict[str, Any]]:
    """One lane per bus; ``private`` marks a lane the gateway and OBD-II port do not reach."""
    lanes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bus in buses:
        code = bus["code"]
        if code.upper() == ALL_CODE or code in seen:
            continue
        seen.add(code)
        lanes.append(
            {
                "code": code,
                "name": bus.get("name") or code,
                "bitrate": bus.get("bitrate"),
                "private": bool(bus.get("private")),
            }
        )
    for ecu in ecus:  # codes the vocabulary does not declare still need a lane
        for code in ecu["buses"]:
            if code.upper() != ALL_CODE and code not in seen:
                seen.add(code)
                lanes.append({"code": code, "name": code, "bitrate": None, "private": False})
    # Private lanes go last so the gateway's connector never has to cross one.
    return sorted(lanes, key=lambda lane: lane["private"])


def layout_buses(buses: Sequence[Mapping], ecus: Sequence[Mapping]) -> dict[str, Any]:
    """Lanes and positioned nodes: ``{"lanes": [...], "nodes": [...], "columns": n}``.

    Each node is ``{name, kind, lanes: [lane index, ...], col}`` where ``kind`` is
    ``ecu``, ``gateway`` or ``obd``. Spanning nodes are placed first, then bridges, then
    single-lane ECUs; each takes the lowest column free on every lane it spans.
    """
    lanes = _lanes(buses, ecus)
    index = {lane["code"]: i for i, lane in enumerate(lanes)}
    reached = [i for i, lane in enumerate(lanes) if not lane["private"]]

    gateway_nodes, bridges, singles, unplaced = [], [], [], []
    for ecu in ecus:
        is_gateway = any(c.upper() == ALL_CODE for c in ecu["buses"])
        members = reached if is_gateway else sorted({index[c] for c in ecu["buses"]})
        if not members:
            unplaced.append(ecu["name"])
            continue
        node = {"name": ecu["name"], "kind": "gateway" if is_gateway else "ecu", "lanes": members}
        (gateway_nodes if is_gateway else bridges if len(members) > 1 else singles).append(node)

    placed_all = any(
        c["code"].upper() == ALL_CODE and "OBD" in (c.get("description") or "").upper()
        for c in buses
    )
    obd = [{"name": "OBD-II", "kind": "obd", "lanes": reached}] if placed_all and reached else []

    if unplaced:
        lanes.append({"code": UNPLACED, "name": "Unplaced", "bitrate": None, "private": False})
        singles += [{"name": name, "kind": "ecu", "lanes": [len(lanes) - 1]} for name in unplaced]

    taken: set[tuple[int, int]] = set()
    nodes = []
    for node in [*obd, *gateway_nodes, *bridges, *singles]:
        # A bridge's connector crosses every lane between its first and last, so it
        # reserves those cells too; another node there would be drawn on top of it.
        spanned = range(min(node["lanes"]), max(node["lanes"]) + 1)
        col = 0
        while any((lane, col) in taken for lane in spanned):
            col += 1
        taken.update((lane, col) for lane in spanned)
        nodes.append({**node, "col": col})

    return {
        "lanes": lanes,
        "nodes": nodes,
        "columns": max((n["col"] for n in nodes), default=-1) + 1,
    }
