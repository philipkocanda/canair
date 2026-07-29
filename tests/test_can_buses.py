"""Tests for canlib.can_buses — per-profile CAN bus vocabulary loader."""

from canlib.can_buses import allowed_can_buses, bus_names, load_can_bus_codes, load_can_buses
from canlib.profile import Profile


def _profile(tmp_path, text=None):
    if text is not None:
        (tmp_path / "can_buses.yaml").write_text(text)
    return Profile(tmp_path.name, tmp_path)


def test_absent_file_is_empty(tmp_path):
    prof = _profile(tmp_path)
    assert load_can_bus_codes(prof) == []
    assert allowed_can_buses(prof) == set()
    assert load_can_buses(prof) == []


def test_loads_mapping_with_names(tmp_path):
    prof = _profile(
        tmp_path,
        "can_buses:\n"
        "  All:\n    name: All segments\n    description: gateway\n"
        "  B:\n    name: Body CAN\n",
    )
    buses = load_can_buses(prof)
    assert [b.code for b in buses] == ["All", "B"]
    assert buses[0].name == "All segments"
    assert buses[0].description == "gateway"
    assert bus_names(prof) == {"All": "All segments", "B": "Body CAN"}
    assert allowed_can_buses(prof) == {"All", "B"}


def test_label_falls_back_to_code(tmp_path):
    prof = _profile(tmp_path, "can_buses:\n  X: {}\n")
    assert load_can_buses(prof)[0].label == "X"


def test_loads_bitrate(tmp_path):
    prof = _profile(
        tmp_path,
        "can_buses:\n"
        "  All:\n    name: All segments\n"
        "  B:\n    name: Body CAN\n    bitrate: 100000\n"
        "  D:\n    name: Diagnostic CAN\n    bitrate: 500000\n",
    )
    buses = {b.code: b for b in load_can_buses(prof)}
    assert buses["All"].bitrate is None
    assert buses["B"].bitrate == 100000
    assert buses["D"].bitrate == 500000


def test_invalid_bitrate_is_none(tmp_path):
    prof = _profile(tmp_path, "can_buses:\n  B:\n    bitrate: fast\n")
    assert load_can_buses(prof)[0].bitrate is None


def test_legacy_list_form(tmp_path):
    prof = _profile(tmp_path, "can_buses:\n  - All\n  - B\n")
    assert load_can_bus_codes(prof) == ["All", "B"]
    assert bus_names(prof) == {"All": "All", "B": "B"}


def test_drops_blanks_and_dups(tmp_path):
    prof = _profile(tmp_path, "can_buses:\n  - B\n  - ' '\n  - B\n  - P\n")
    assert load_can_bus_codes(prof) == ["B", "P"]


def test_non_container_is_empty(tmp_path):
    prof = _profile(tmp_path, "can_buses: notacontainer\n")
    assert load_can_bus_codes(prof) == []
