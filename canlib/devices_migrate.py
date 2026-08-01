"""One-time migration of the legacy ``wican_addresses:`` config block.

The richer ``devices:`` block supersedes the flat ``wican_addresses`` map (see
:func:`canlib.config.wican_devices`): each ``alias: ip`` becomes
``alias: {host: ip}``, gaining optional per-device ``transport``/``port``/
``bitrate``. This module rewrites an old config into the new shape in place,
comment-preservingly, so users never have to.

It is intentionally *transitional*: there is no manual ``config`` subcommand for
it. :func:`maybe_auto_migrate` runs once at startup (best-effort — any failure is
swallowed, and runtime precedence in :func:`canlib.config.wican_devices` keeps a
legacy config working regardless). Detection is self-clearing: migration only
fires when ``wican_addresses`` is present *and* ``devices`` is absent, which is
false after one successful run.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .config import load_config, user_config_file


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of a migration attempt."""

    migrated: bool
    path: Path
    reason: str | None = None  # why nothing happened (when migrated is False)


def _needs_migration(cfg: dict) -> bool:
    """True when config has a legacy ``wican_addresses`` and no ``devices`` block."""
    has_devices = isinstance(cfg.get("devices"), dict) and bool(cfg.get("devices"))
    has_addrs = isinstance(cfg.get("wican_addresses"), dict) and bool(cfg.get("wican_addresses"))
    return has_addrs and not has_devices


def migrate_config(*, dry_run: bool = False) -> MigrationResult:
    """Rewrite ``wican_addresses:`` → ``devices:`` in the user config file.

    Comment-preserving (ruamel round-trip): each alias's value becomes
    ``{host: <ip>}`` and its inline/block comments are carried over; the new
    ``devices`` block takes the old block's position and ``wican_addresses`` is
    removed. No-op (with a ``reason``) when there's nothing to migrate. When
    ``dry_run`` is set, nothing is written.
    """
    from ruamel.yaml.comments import CommentedMap

    from .yaml_rt import dump, round_trip_yaml

    path = user_config_file()
    if not path.exists():
        return MigrationResult(False, path, "no user config file")

    if not _needs_migration(load_config()):
        return MigrationResult(False, path, "no legacy wican_addresses to migrate")

    text = path.read_text()
    data = round_trip_yaml().load(text) if text.strip() else None
    if not isinstance(data, dict) or "wican_addresses" not in data:
        return MigrationResult(False, path, "no legacy wican_addresses to migrate")

    old = data["wican_addresses"]
    if not isinstance(old, dict) or not old:
        return MigrationResult(False, path, "wican_addresses is empty or malformed")

    # Build the devices map, reusing each original value scalar so quoting is
    # preserved, and carrying over each alias's inline comment onto the new
    # `host:` line (keeping the alias line clean above its nested mapping).
    devices = CommentedMap()
    old_item_ca = getattr(getattr(old, "ca", None), "items", {})
    for alias, host in old.items():
        inner = CommentedMap([("host", host)])
        if alias in old_item_ca:
            inner.ca.items["host"] = old_item_ca[alias]
        devices[alias] = inner

    # Insert `devices` where `wican_addresses` sat, carry the block's key-level
    # comments (e.g. the explanatory block above it), then drop the old key.
    top_ca = data.ca.items
    block_ca = top_ca.get("wican_addresses")
    pos = list(data).index("wican_addresses")
    data.insert(pos, "devices", devices)
    del data["wican_addresses"]
    if block_ca is not None:
        top_ca["devices"] = block_ca
        top_ca.pop("wican_addresses", None)

    if not dry_run:
        with open(path, "w") as f:
            dump(data, f)
        load_config.cache_clear()

    return MigrationResult(True, path)


def maybe_auto_migrate() -> None:
    """Best-effort auto-migration at startup; prints a one-line notice on success.

    Never raises: any IO/parse error is swallowed (runtime precedence in
    :func:`canlib.config.wican_devices` keeps a legacy config working). Runs on
    any invocation, at most once (detection is self-clearing).
    """
    try:
        if not _needs_migration(load_config()):
            return
        result = migrate_config()
        if result.migrated:
            print(
                f"note: migrated wican_addresses → devices: in {result.path} "
                "(richer per-device transport config; see `canair config show`)",
                file=sys.stderr,
            )
    except Exception:
        # Best-effort — a failed migration must never break a command.
        pass
