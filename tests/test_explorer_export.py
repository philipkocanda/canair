"""Tests for the docs explorer export and bus layout (canlib/explorer_*.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from canlib.explorer_export import export_profile, profile_maturity
from canlib.explorer_layout import layout_buses

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES = sorted(
    p
    for p in (REPO_ROOT / "profiles").iterdir()
    if (p / "ecus").is_dir() and (p / "profile.yaml").exists()
)


# Identity fields that identify one physical car or part. Free-text notes can mention a public
# software id; these must never appear anywhere in the export.
PII_IDENTITY_FIELDS = ("vin", "serial", "part_number", "diag_address", "mfg_date")


def _ids(paths):
    return [p.name for p in paths]


@pytest.fixture(scope="module", params=PROFILES, ids=_ids(PROFILES))
def exported(request):
    root: Path = request.param
    return root, export_profile(root)


def _records(data):
    """Every exported record that carries a ``src`` and the key text it must contain."""
    for ecu in data["ecus"]:
        yield ecu["src"], ecu["name"]
        for pid in ecu["pids"]:
            yield pid["src"], pid["id"]
            for sig in pid["signals"]:
                yield sig["src"], sig["name"]
        for cmd in ecu["iocontrol"]:
            yield cmd["src"], cmd["did"]
        for dtc in ecu["dtcs"]:
            yield dtc["src"], dtc["code"]
    for msg in data["broadcast"]:
        yield msg["src"], msg["id"]
        for sig in msg["signals"]:
            yield sig["src"], sig["name"]


def test_export_is_deterministic_and_json_serialisable(exported):
    root, data = exported
    assert json.dumps(data) == json.dumps(export_profile(root))


def test_stats_match_the_profiles_index_counts(exported):
    root, data = exported
    pytest.importorskip("mkdocs")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "profiles_index_hook", REPO_ROOT / "scripts" / "mkdocs_hooks" / "profiles_index.py"
    )
    assert spec and spec.loader
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    expected = hook.profile_stats(root / "ecus")
    assert data["profile"]["stats"] == expected


def test_every_source_line_contains_the_records_key(exported):
    _root, data = exported
    cache: dict[str, list[str]] = {}
    checked = 0
    for src, key in _records(data):
        lines = cache.setdefault(src["file"], (REPO_ROOT / src["file"]).read_text().splitlines())
        assert 1 <= src["line"] <= len(lines), (src, key)
        assert key.lower() in lines[src["line"] - 1].lower(), (src, key, lines[src["line"] - 1])
        checked += 1
    assert checked > 0


def test_no_identity_values_are_exported(exported):
    root, data = exported
    from canlib.yaml_rt import round_trip_yaml

    secrets: set[str] = set()
    for path in (root / "ecus").glob("*.yaml"):
        for ecu in (round_trip_yaml().load(path.read_text()) or {}).values():
            identity = (ecu or {}).get("identity") or {}
            for field, value in identity.items():
                if field in PII_IDENTITY_FIELDS and isinstance(value, str) and len(value) >= 6:
                    secrets.add(value)
    blob = json.dumps(data, ensure_ascii=False)
    leaked = sorted(s for s in secrets if s in blob)
    assert not leaked, f"identity values leaked into the export: {leaked[:5]}"


def test_no_capture_data_is_exported(exported):
    _root, data = exported
    forbidden = {"captures", "scan_log", "identity", "payload"}
    assert not forbidden & set(data)
    assert not forbidden & set(data["ecus"][0] if data["ecus"] else {})


def test_layout_places_every_ecu_on_exactly_its_lanes(exported):
    _root, data = exported
    layout = data["layout"]
    codes = [lane["code"] for lane in layout["lanes"]]
    nodes = {n["name"]: n for n in layout["nodes"] if n["kind"] != "obd"}
    assert set(nodes) == {e["name"] for e in data["ecus"]}
    for ecu in data["ecus"]:
        node = nodes[ecu["name"]]
        placed = {codes[i] for i in node["lanes"]}
        if not ecu["buses"]:
            assert placed == {"unplaced"}
        elif any(b.upper() == "ALL" for b in ecu["buses"]):
            private = {b["code"] for b in data["buses"] if b["private"]}
            assert placed == {c for c in codes if c != "unplaced" and c not in private}
        else:
            assert placed == set(ecu["buses"])


def test_layout_never_overlaps_two_nodes():
    layout = layout_buses(
        [{"code": "A"}, {"code": "B"}, {"code": "ALL", "description": "the OBD-II port"}],
        [
            {"name": "GW", "buses": ["ALL"]},
            {"name": "BRIDGE", "buses": ["A", "B"]},
            {"name": "ONLY_A", "buses": ["A"]},
            {"name": "LOOSE", "buses": []},
            {"name": "ODD", "buses": ["Z"]},
        ],
    )
    cells = [
        (lane, n["col"])
        for n in layout["nodes"]
        for lane in range(min(n["lanes"]), max(n["lanes"]) + 1)
    ]
    assert len(cells) == len(set(cells))
    assert [lane["code"] for lane in layout["lanes"]] == ["A", "B", "Z", "unplaced"]
    assert {n["name"]: n["kind"] for n in layout["nodes"]}["OBD-II"] == "obd"


def test_layout_keeps_private_lanes_out_of_gateway_and_obd_spans():
    layout = layout_buses(
        [
            {"code": "ALL", "description": "the OBD-II port"},
            {"code": "PRIV", "private": True},
            {"code": "A"},
            {"code": "B"},
        ],
        [{"name": "GW", "buses": ["ALL"]}, {"name": "HVAC", "buses": ["A", "PRIV"]}],
    )
    codes = [lane["code"] for lane in layout["lanes"]]
    assert codes == ["A", "B", "PRIV"]
    nodes = {n["name"]: n for n in layout["nodes"]}
    for name in ("GW", "OBD-II"):
        assert {codes[i] for i in nodes[name]["lanes"]} == {"A", "B"}
    assert {codes[i] for i in nodes["HVAC"]["lanes"]} == {"A", "PRIV"}


def test_layout_without_any_bus_data_puts_everything_in_unplaced():
    layout = layout_buses([], [{"name": "X", "buses": []}, {"name": "GW", "buses": ["ALL"]}])
    assert [lane["code"] for lane in layout["lanes"]] == ["unplaced"]
    assert sorted(n["name"] for n in layout["nodes"]) == ["GW", "X"]


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({"ecus": 30, "params": 400, "verified": 20}, "mature"),
        ({"ecus": 1, "params": 1, "verified": 0}, "seed / stub"),
        ({"ecus": 5, "params": 40, "verified": 3}, "early"),
    ],
)
def test_profile_maturity(counts, expected):
    assert profile_maturity(counts) == expected
