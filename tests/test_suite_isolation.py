"""Guards on the test suite's own environment isolation.

Two pieces of ambient state would otherwise make results depend on the machine
the suite runs on:

* the **user config** (``$XDG_CONFIG_HOME/canair/config.yaml``) — read whenever a
  profile is resolved (``resolve_profile`` consults ``profiles_dir``), so a
  malformed value there broke tests with nothing to do with config, and a
  ``default_profile``/``devices``/``transport`` block silently changed which
  profile or transport a test resolved;
* the **active profile** (``CANAIR_PROFILE``), pinned because the repo bundles
  more than one and auto-resolution is ambiguous.

``tests/conftest.py`` pins both. These tests fail if that pinning regresses —
otherwise the breakage is invisible on a developer machine whose real config
happens to be benign, and only shows up as a mystery failure for someone else.
"""

from __future__ import annotations

import os
from pathlib import Path

from canlib import config, profile


class TestUserConfigIsolation:
    def test_xdg_config_home_is_pinned_away_from_the_real_home(self):
        xdg = os.environ.get("XDG_CONFIG_HOME")
        assert xdg, "conftest must pin XDG_CONFIG_HOME for the suite"
        real_home_config = Path.home() / ".config"
        assert Path(xdg).resolve() != real_home_config.resolve(), (
            "the suite must not read the developer's real ~/.config"
        )

    def test_resolved_config_dir_is_not_the_developers(self):
        assert config.config_dir().resolve() != (Path.home() / ".config" / "canair").resolve()

    def test_the_pinned_config_has_no_active_settings(self):
        """Any config in the pinned dir carries no real settings.

        `cli.main()` seeds a commented-out starter `config.yaml` on first run, so
        the file may legitimately exist once a test has invoked the CLI. What must
        never happen is a *populated* config — that would mean the developer's own
        file (or a leaked write) is steering the suite.
        """
        path = config.user_config_file()
        if not path.exists():
            return
        active = [
            line
            for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert active == [], f"pinned config has real settings: {active}"

    def test_load_config_sees_no_user_settings(self):
        config.load_config.cache_clear()
        try:
            cfg = config.load_config()
        finally:
            config.load_config.cache_clear()
        # A developer's real `default_profile`, `devices`, or `transport` block
        # must not leak into the suite. (The seeded starter config is entirely
        # commented out, so this holds whether or not it has been written.)
        for key in ("devices", "default_wican", "transport", "wican_addresses"):
            assert key not in cfg, f"{key!r} leaked from a real user config"


class TestActiveProfilePinning:
    def test_canair_profile_is_pinned(self):
        assert os.environ.get("CANAIR_PROFILE") == "ioniq-2017"

    def test_active_profile_resolves_to_the_bundled_reference(self):
        assert profile.resolve_profile().name == "ioniq-2017"


class TestNoEagerConfigReadAtImportTime:
    """Importing canair must not read the user config.

    ``canlib.constants`` resolves config-dependent constants lazily (PEP 562) so
    the active profile is honoured at *access* time. Two modules defeated that by
    importing ``WICAN_ADDRESSES``/``DEFAULT_WICAN`` at module scope, which fired
    the lazy resolver during import — i.e. during pytest *collection*, before any
    fixture could isolate it. A malformed real config then failed collection
    outright, and no fixture could intervene.
    """

    def test_importing_live_runtime_does_not_read_the_config(self, monkeypatch):
        import importlib

        called: list[str] = []
        real_load = config.load_config.__wrapped__  # bypass the lru_cache

        def spy():
            called.append("load_config")
            return real_load()

        config.load_config.cache_clear()
        monkeypatch.setattr(config, "load_config", spy)

        # Re-import the modules that previously resolved a config-backed default
        # at module scope.
        for mod in ("canlib.wican_api", "canlib.commands.wican", "canlib.commands._live"):
            importlib.reload(importlib.import_module(mod))

        assert called == [], (
            "importing canair read the user config; keep config-dependent "
            "constants inside function bodies so they resolve at access time"
        )

    def test_live_defaults_do_not_bake_in_a_device_address(self):
        from canlib.commands._live import CANAIR_DEFAULTS

        # Must stay None (matching the real `--wican` default). A config-derived
        # value here is resolved at import time and re-introduces the eager read.
        assert CANAIR_DEFAULTS["wican"] is None
