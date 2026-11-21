"""CAN bus vocabulary edits preserve data and reject invalid writes."""

import pytest

from canair.core import yaml_io
from canair.profile.model import Profile
from canair.yaml_edit.can_buses import BusEditError, upsert_bus


def test_create_and_update(tmp_path):
    profile = Profile("test", tmp_path)
    upsert_bus("H-CAN", profile=profile, name="High-speed CAN", private=True)
    upsert_bus("H-CAN", profile=profile, description="A private link", bitrate=500000)
    data = yaml_io.safe_load(profile.can_buses_file.read_text())
    assert data["can_buses"]["H-CAN"] == {
        "name": "High-speed CAN",
        "description": "A private link",
        "private": True,
        "bitrate": 500000,
    }
    upsert_bus("H-CAN", profile=profile, private=False)
    data = yaml_io.safe_load(profile.can_buses_file.read_text())
    assert data["can_buses"]["H-CAN"]["private"] is False


def test_preserves_comments_and_legacy_entry_name(tmp_path):
    profile = Profile("test", tmp_path)
    profile.can_buses_file.write_text("# header\ncan_buses:\n  OLD: Old bus  # keep\n")
    upsert_bus("OLD", profile=profile, private=True)
    text = profile.can_buses_file.read_text()
    assert "# header" in text and "# keep" in text
    assert yaml_io.safe_load(text)["can_buses"]["OLD"]["name"] == "Old bus"


def test_invalid_write_reverts(tmp_path):
    profile = Profile("test", tmp_path)
    profile.can_buses_file.write_text("can_buses:\n  P-CAN: {}\n")
    original = profile.can_buses_file.read_text()
    with pytest.raises(BusEditError):
        upsert_bus("H-CAN", profile=profile, bitrate=0)
    assert profile.can_buses_file.read_text() == original


def test_legacy_list_is_not_rewritten(tmp_path):
    profile = Profile("test", tmp_path)
    profile.can_buses_file.write_text("can_buses: [P-CAN, ALL] # preserve\n")
    original = profile.can_buses_file.read_text()
    with pytest.raises(BusEditError):
        upsert_bus("H-CAN", profile=profile)
    assert profile.can_buses_file.read_text() == original


def test_invalid_code_does_not_create_file(tmp_path):
    profile = Profile("test", tmp_path)
    with pytest.raises(BusEditError):
        upsert_bus(" H-CAN", profile=profile)
    assert not profile.can_buses_file.exists()
