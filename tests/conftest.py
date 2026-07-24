"""Shared test fixtures.

The repo bundles more than one vehicle profile (e.g. ``ioniq-2017`` and
``ioniq-5-2022``), so profile auto-resolution is ambiguous. Pin the bundled
``ioniq-2017`` profile for the whole suite so tests that load the real profile
stay deterministic. Individual tests may still override this via
``monkeypatch.setenv("CANAIR_PROFILE", ...)`` or by passing an explicit profile.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _pin_bundled_profile():
    """Session-wide default so module-scoped fixtures see it too."""
    prev = os.environ.get("CANAIR_PROFILE")
    os.environ["CANAIR_PROFILE"] = "ioniq-2017"
    yield
    if prev is None:
        os.environ.pop("CANAIR_PROFILE", None)
    else:
        os.environ["CANAIR_PROFILE"] = prev


@pytest.fixture(autouse=True)
def _reset_active_profile():
    """Clear the memoized active profile around each test."""
    import canlib.profile as profile

    profile._active = None
    yield
    profile._active = None
