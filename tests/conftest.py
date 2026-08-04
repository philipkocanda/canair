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


@pytest.fixture(scope="session", autouse=True)
def _isolate_user_config(tmp_path_factory):
    """Point ``$XDG_CONFIG_HOME`` at a throwaway directory for the whole suite.

    Resolving the active profile (``canlib.profile.resolve_profile`` →
    ``load_config().get("profiles_dir")``) legitimately reads the user config
    file to see whether the profile search path was overridden — so *any* test
    that loads a real profile (most of them) reads ``config_dir()`` at some
    point, not just tests that exercise ``canlib.config`` directly. Left
    unpinned, the suite's outcome depends on whatever happens to be in the
    developer's real ``~/.config/canair/config.yaml`` (a malformed value there
    fails collection/tests that have nothing to do with config; a `default_
    profile`/`devices` block there silently changes which profile or transport
    a test resolves). Point every test at an empty, deterministic directory
    instead; a test that needs specific config content still writes into it or
    overrides ``XDG_CONFIG_HOME`` itself via ``monkeypatch.setenv`` (which wins
    for the duration of that test and is restored to this default afterward,
    not to the developer's real environment).
    """
    path = tmp_path_factory.mktemp("xdg_config_home")
    prev = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(path)
    yield
    if prev is None:
        os.environ.pop("XDG_CONFIG_HOME", None)
    else:
        os.environ["XDG_CONFIG_HOME"] = prev


@pytest.fixture(autouse=True)
def _reset_active_profile():
    """Clear the memoized active profile around each test."""
    import canlib.profile as profile

    profile._active = None
    yield
    profile._active = None


@pytest.fixture(scope="session", autouse=True)
def _isolate_event_log(tmp_path_factory):
    """Redirect the central diagnostics event log to a temp file for the suite.

    Transport exchanges log drops/errors to ``config_dir()/logs/canair.log``; left
    unpinned, the test suite would scribble into the developer's real user log.
    Point it at a throwaway path (and clear any handler a test opened).
    """
    import logging

    import canlib.log as clog

    path = tmp_path_factory.mktemp("eventlog") / "canair.log"
    clog.event_log_path = lambda: path  # type: ignore[assignment]
    clog._event_logger = None
    logging.getLogger("canair.events").handlers.clear()
    yield
