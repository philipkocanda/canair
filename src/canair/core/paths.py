"""Shared static path roots and repo constants.

Everything here is a **static** value known at import time — path roots
(``PACKAGE_DIR``, ``REPO_ROOT``, ``BUNDLED_PROFILES_DIR``, ``TEMPLATES_DIR``,
``SCHEMA_DIR``), the legacy config path, and the upstream repo.

Each path says what it means. ``PACKAGE_DIR`` is the installed package. The shipped data
(profiles, templates, the example config) is ``PACKAGE_DIR/_bundled`` in a wheel and the repo
root in a checkout. ``REPO_ROOT`` is the checkout and ``None`` in a wheel, so code that needs a
git working tree cannot mistake ``site-packages`` for one.

Profile- and config-dependent values are deliberately **not** here. They used to
be, resolved through a module-level ``__getattr__`` (PEP 562) — which made them
untyped at every import site and, because a plain ``from … import`` triggers the
hook, read the user's config file at *import* time (during pytest collection,
before any fixture could isolate it). Use the explicit accessors instead:

- devices/aliases → :func:`canair.config.wican_addresses` /
  :func:`canair.config.default_wican`
- vehicle-data paths → ``canair.profile.active().ecus_dir`` / ``.captures_dir``
"""

import os
from pathlib import Path


def resolve_roots(package_dir: Path) -> tuple[Path | None, Path]:
    """``(repo root, data root)`` for a package at ``package_dir``.

    A checkout has the package at ``<repo>/src/canair`` beside ``pyproject.toml``, and the data
    (profiles, templates, the example config) at the repo root. A wheel has the data in
    ``<package>/_bundled`` and no repo, so the repo root is ``None``.
    """
    bundled = package_dir / "_bundled"
    checkout = package_dir.parent.parent
    in_checkout = package_dir.parent.name == "src" and (checkout / "pyproject.toml").is_file()
    repo = checkout if in_checkout else None
    return repo, bundled if bundled.is_dir() else (repo or package_dir.parent)


PACKAGE_DIR = Path(__file__).parent.parent  # src/canair/ in a checkout, site-packages/canair/
REPO_ROOT, DATA_ROOT = resolve_roots(PACKAGE_DIR)
BUNDLED_PROFILES_DIR = DATA_ROOT / "profiles"  # profiles shipped with canair
TEMPLATES_DIR = DATA_ROOT / "templates"  # scaffold templates for `profile create`
SCHEMA_DIR = PACKAGE_DIR / "schema"  # tool-owned YAML/JSON schemas

# Legacy repo-local WiCAN config (deprecated in favor of ~/.config/canair/config.yaml)
CONFIG_FILE = DATA_ROOT / "config.yaml"

# GitHub repository canair is developed under — the source for releases
# (`canair update`) and the target for contributions (`canair contribute`).
GITHUB_REPO = "philipkocanda/canair"

# Hosted docs site (mkdocs `site_url`) — for doc pointers in --help text.
DOCS_URL = "https://philipkocanda.github.io/canair"
CONFIG_DOCS_URL = f"{DOCS_URL}/reference/config/"

# Editor-facing JSON Schema for profile.yaml, published on the docs site
# (scripts/mkdocs_hooks/schemas.py). Every profile.yaml carries the modeline so
# yaml-language-server editors use it instead of guessing one from the filename.
PROFILE_SCHEMA_FILE = SCHEMA_DIR / "profile_schema.json"
PROFILE_SCHEMA_URL = f"{DOCS_URL}/schema/profile.json"
PROFILE_SCHEMA_MODELINE = f"# yaml-language-server: $schema={PROFILE_SCHEMA_URL}"

# Commented, fully-documented example config — the authoritative reference for
# every key (see `canair config example`). Force-included into the wheel
# alongside `profiles`/`templates` so it's present in an installed copy too.
CONFIG_EXAMPLE_FILE = DATA_ROOT / "config.example.yaml"


def config_dir() -> Path:
    """Return the canair config directory ($XDG_CONFIG_HOME/canair)."""
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "canair"
