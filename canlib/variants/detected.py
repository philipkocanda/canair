"""Detected ECU revisions, remembered between sessions (local user state).

Detection costs a read per ambiguous ECU, and a sleeping ECU answers nothing, so a
revision once detected is kept in ``<config dir>/state/detected_revisions.yaml``,
keyed by profile and variant::

    hyundai-ioniq-ae-ev:
      38kwh:
        BMS: uds

It is a fact about *your* car, so it lives beside the user config, never in the profile
(it is not contributed). A pin always outranks it; a later identity read that
contradicts it replaces it. ``""`` is the key for a reading made with no variant chosen.
"""

from __future__ import annotations

from pathlib import Path

from canlib import yaml_io

NO_VARIANT = ""


def cache_file() -> Path:
    from canlib.config import config_dir

    return config_dir() / "state" / "detected_revisions.yaml"


def _load() -> dict:
    path = cache_file()
    if not path.exists():
        return {}
    try:
        data = yaml_io.safe_load(path.read_text()) or {}
    except Exception:
        return {}  # a corrupt cache is only a lost shortcut; detection runs again
    return data if isinstance(data, dict) else {}


def remembered(profile: str, variant: str | None) -> dict[str, str]:
    """ECU -> detected revision for ``profile`` and ``variant`` (the no-variant entry
    fills any ECU the variant's own entry lacks)."""
    entry = _load().get(profile)
    if not isinstance(entry, dict):
        return {}
    out: dict[str, str] = {}
    for key in (NO_VARIANT, variant or NO_VARIANT):
        block = entry.get(key)
        if isinstance(block, dict):
            out.update({str(e): str(r) for e, r in block.items() if r})
    return out


def remember(profile: str, variant: str | None, ecu: str, revision: str) -> bool:
    """Record a detection; returns whether the cache changed."""
    import yaml

    data = _load()
    block = data.setdefault(profile, {}).setdefault(variant or NO_VARIANT, {})
    if block.get(ecu) == revision:
        return False
    block[ecu] = revision
    path = cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Detected ECU revisions (canair): local state about your car, safe to delete.\n"
        + yaml.safe_dump(data, sort_keys=True)
    )
    return True


def forget(profile: str, variant: str | None, ecu: str) -> bool:
    """Drop a detection (a later reading contradicted it and settled nothing)."""
    import yaml

    data = _load()
    block = (data.get(profile) or {}).get(variant or NO_VARIANT)
    if not isinstance(block, dict) or ecu not in block:
        return False
    del block[ecu]
    cache_file().write_text(
        "# Detected ECU revisions (canair): local state about your car, safe to delete.\n"
        + yaml.safe_dump(data, sort_keys=True)
    )
    return True
