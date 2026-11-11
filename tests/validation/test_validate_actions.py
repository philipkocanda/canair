"""``validate actions`` checks every car's definitions, not the one the user selected."""

import pytest

from canair.core.paths import BUNDLED_PROFILES_DIR
from canair.profile.model import profile_for_path
from canair.validation.other import check_actions


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CANAIR_VARIANT", raising=False)


def test_bundled_actions_validate_when_no_car_is_chosen(isolated_config):
    profile = profile_for_path(BUNDLED_PROFILES_DIR / "hyundai-ioniq-ae-ev")
    report = check_actions(profile)
    assert report.errors == ()
