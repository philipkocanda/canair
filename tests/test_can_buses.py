"""Tests for canlib.can_buses — per-profile CAN bus vocabulary loader."""

from canlib.can_buses import allowed_can_buses, load_can_bus_codes
from canlib.profile import Profile


def _profile(tmp_path, text=None):
    if text is not None:
        (tmp_path / "can_buses.yaml").write_text(text)
    return Profile(tmp_path.name, tmp_path)


def test_absent_file_is_empty(tmp_path):
    prof = _profile(tmp_path)
    assert load_can_bus_codes(prof) == []
    assert allowed_can_buses(prof) == set()


def test_loads_ordered_codes(tmp_path):
    prof = _profile(tmp_path, "can_buses:\n  - All\n  - B\n  - P\n")
    assert load_can_bus_codes(prof) == ["All", "B", "P"]
    assert allowed_can_buses(prof) == {"All", "B", "P"}


def test_drops_blanks_and_dups(tmp_path):
    prof = _profile(tmp_path, "can_buses:\n  - B\n  - ' '\n  - B\n  - P\n")
    assert load_can_bus_codes(prof) == ["B", "P"]


def test_non_list_is_empty(tmp_path):
    prof = _profile(tmp_path, "can_buses: notalist\n")
    assert load_can_bus_codes(prof) == []
