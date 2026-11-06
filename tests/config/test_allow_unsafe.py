"""``safety.allow_unsafe``: only a boolean true in the user config file enables ``--unsafe``."""

from __future__ import annotations

import pytest

from canair.config import file as config_file
from canair.config.file import unsafe_allowed


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "canair" / "config.yaml"
    path.parent.mkdir(parents=True)
    return path


def test_off_without_a_config_file(user_config):
    assert unsafe_allowed() is False


def test_on_with_a_boolean_true(user_config):
    user_config.write_text("safety:\n  allow_unsafe: true\n")
    assert unsafe_allowed() is True


@pytest.mark.parametrize(
    "text",
    [
        "safety:\n  allow_unsafe: false\n",
        'safety:\n  allow_unsafe: "true"\n',
        "safety:\n  allow_unsafe: 1\n",
        "safety: true\n",
        "allow_unsafe: true\n",
        "- safety\n",
    ],
)
def test_off_for_anything_but_a_boolean_true(user_config, text):
    user_config.write_text(text)
    assert unsafe_allowed() is False


def test_the_legacy_repo_local_config_does_not_count(user_config, tmp_path, monkeypatch):
    legacy = tmp_path / "repo-config.yaml"
    legacy.write_text("safety:\n  allow_unsafe: true\n")
    monkeypatch.setattr(config_file, "CONFIG_FILE", legacy)
    config_file.load_config.cache_clear()
    assert config_file.load_config()["safety"] == {"allow_unsafe": True}
    assert unsafe_allowed() is False


def test_reads_the_file_on_each_call(user_config):
    user_config.write_text("safety:\n  allow_unsafe: true\n")
    assert unsafe_allowed() is True
    user_config.write_text("safety:\n  allow_unsafe: false\n")
    assert unsafe_allowed() is False
