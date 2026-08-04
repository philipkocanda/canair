"""Shared static path roots and repo constants.

Everything here is a **static** value known at import time — path roots
(``PACKAGE_DIR``, ``SCRIPT_DIR``, ``BUNDLED_PROFILES_DIR``, ``TEMPLATES_DIR``,
``SCHEMA_DIR``), the legacy config path, and the upstream repo.

Profile- and config-dependent values are deliberately **not** here. They used to
be, resolved through a module-level ``__getattr__`` (PEP 562) — which made them
untyped at every import site and, because a plain ``from … import`` triggers the
hook, read the user's config file at *import* time (during pytest collection,
before any fixture could isolate it). Use the explicit accessors instead:

- devices/aliases → :func:`canlib.config.wican_addresses` /
  :func:`canlib.config.default_wican`
- vehicle-data paths → ``canlib.profile.active().ecus_dir`` / ``.captures_dir``
"""

from pathlib import Path

PACKAGE_DIR = Path(__file__).parent  # canlib/
SCRIPT_DIR = PACKAGE_DIR.parent  # repo root
BUNDLED_PROFILES_DIR = SCRIPT_DIR / "profiles"  # profiles shipped with the repo
TEMPLATES_DIR = SCRIPT_DIR / "templates"  # scaffold templates for `profile create`
SCHEMA_DIR = PACKAGE_DIR / "schema"  # tool-owned YAML/JSON schemas

# Legacy repo-local WiCAN config (deprecated in favor of ~/.config/canair/config.yaml)
CONFIG_FILE = SCRIPT_DIR / "config.yaml"

# GitHub repository canair is developed under — the source for releases
# (`canair update`) and the target for contributions (`canair contribute`).
GITHUB_REPO = "philipkocanda/canair"
