"""Tests for canlib.can_buses — per-profile CAN bus vocabulary loader."""

from canlib.can_buses import (
    ALL_CODE,
    allowed_can_buses,
    bus_names,
    expand_bus_membership,
    load_can_bus_codes,
    load_can_buses,
)
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
        "  ALL:\n    name: All segments\n    description: gateway\n"
        "  B-CAN:\n    name: Body CAN\n",
    )
    buses = load_can_buses(prof)
    assert [b.code for b in buses] == ["ALL", "B-CAN"]
    assert buses[0].name == "All segments"
    assert buses[0].description == "gateway"
    assert bus_names(prof) == {"ALL": "All segments", "B-CAN": "Body CAN"}
    assert allowed_can_buses(prof) == {"ALL", "B-CAN"}


def test_label_falls_back_to_code(tmp_path):
    prof = _profile(tmp_path, "can_buses:\n  X: {}\n")
    assert load_can_buses(prof)[0].label == "X"


def test_loads_bitrate(tmp_path):
    prof = _profile(
        tmp_path,
        "can_buses:\n"
        "  ALL:\n    name: All segments\n"
        "  B-CAN:\n    name: Body CAN\n    bitrate: 100000\n"
        "  D-CAN:\n    name: Diagnostic CAN\n    bitrate: 500000\n",
    )
    buses = {b.code: b for b in load_can_buses(prof)}
    assert buses["ALL"].bitrate is None
    assert buses["B-CAN"].bitrate == 100000
    assert buses["D-CAN"].bitrate == 500000


def test_invalid_bitrate_is_none(tmp_path):
    prof = _profile(tmp_path, "can_buses:\n  B-CAN:\n    bitrate: fast\n")
    assert load_can_buses(prof)[0].bitrate is None


def test_legacy_list_form(tmp_path):
    prof = _profile(tmp_path, "can_buses:\n  - ALL\n  - B-CAN\n")
    assert load_can_bus_codes(prof) == ["ALL", "B-CAN"]
    assert bus_names(prof) == {"ALL": "ALL", "B-CAN": "B-CAN"}


def test_drops_blanks_and_dups(tmp_path):
    prof = _profile(tmp_path, "can_buses:\n  - B-CAN\n  - ' '\n  - B-CAN\n  - P-CAN\n")
    assert load_can_bus_codes(prof) == ["B-CAN", "P-CAN"]


def test_non_container_is_empty(tmp_path):
    prof = _profile(tmp_path, "can_buses: notacontainer\n")
    assert load_can_bus_codes(prof) == []


DECLARED = ["ALL", "B-CAN", "P-CAN", "D-CAN"]


def test_expand_all_covers_every_declared_bus():
    # A gateway on ALL is a member of every declared segment (incl. the
    # diagnostic bus), plus the ALL code itself.
    assert expand_bus_membership(["ALL"], DECLARED) == {"ALL", "B-CAN", "P-CAN", "D-CAN"}


def test_expand_all_is_case_insensitive():
    assert expand_bus_membership(["All"], DECLARED) == set(DECLARED)
    assert ALL_CODE == "ALL"


def test_expand_plain_codes_pass_through():
    assert expand_bus_membership(["B-CAN", "P-CAN"], DECLARED) == {"B-CAN", "P-CAN"}


def test_expand_ignores_blanks():
    assert expand_bus_membership([" ", ""], DECLARED) == set()
