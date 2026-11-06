"""Validation of ``profile.yaml``: its schema, plus the rules a schema cannot state."""

import re
from pathlib import Path
from typing import Any

import yaml

from canair.core import yaml_io
from canair.core.json_schema import schema_errors
from canair.core.paths import PROFILE_SCHEMA_FILE
from canair.safety.quirks import RETIRED_QUIRKS

from ._common import duplicate_key_errors


def validate_meta(path: Path) -> list[str]:
    """Validate profile.yaml (profile-wide settings).

    ``profile_schema.json`` checks the structure: required fields, types, ranges, the
    ``isotp:`` and ``addressing:`` keys and the quirk vocabulary. Unknown top-level keys are
    tolerated (profile.yaml stays extensible). The checks below are the ones a schema
    cannot make.
    """
    try:
        text = path.read_text()
        data = yaml_io.safe_load(text)
    except yaml.YAMLError as e:
        return [f"profile.yaml: YAML parse error: {e}"]

    if not data or not isinstance(data, dict):
        return ["profile.yaml: empty or invalid"]

    errors = duplicate_key_errors(text) + schema_errors(PROFILE_SCHEMA_FILE, data)
    errors += _rx_offset_errors(data.get("addressing"))
    errors += _physical_band_order_errors(data.get("physical_bands"))
    return [f"profile.yaml: {e}" for e in errors]


_SIGNED_HEX = re.compile(r"^[+-]0[xX][0-9a-fA-F]+$")


def _rx_offset_errors(addressing: Any) -> list[str]:
    """The schema also accepts ``"-0x20"``, which is how a YAML 1.2 editor reads ``-0x20``.

    PyYAML reads ``-0x20`` as an integer, so a string here was quoted, and canair cannot use it.
    """
    offset = addressing.get("rx_offset") if isinstance(addressing, dict) else None
    if isinstance(offset, str) and _SIGNED_HEX.match(offset):
        return [f"addressing.rx_offset: must be an integer, not quoted text (got {offset!r})"]
    return []


def _physical_band_order_errors(bands: Any) -> list[str]:
    """Each ``physical_bands`` range must run from low to high."""
    if not isinstance(bands, dict):
        return []
    errors = []
    for name, rng in bands.items():
        if not (isinstance(rng, list) and len(rng) == 2):
            continue
        lo, hi = rng
        numbers = all(isinstance(v, int | float) and not isinstance(v, bool) for v in rng)
        if numbers and not lo < hi:
            errors.append(f"physical_bands.{name}: low ({lo}) must be less than high ({hi})")
    return errors


def retired_quirk_warnings(path: Path) -> list[str]:
    """A warning per retired quirk the profile.yaml still lists (see ``RETIRED_QUIRKS``)."""

    try:
        data = yaml_io.safe_load(path.read_text())
    except yaml.YAMLError:
        return []  # validate_meta reports the parse error
    quirks = data.get("quirks") if isinstance(data, dict) else None
    if not isinstance(quirks, list):
        return []
    return [
        f"profile.yaml: quirk '{q}' is retired and does nothing: {RETIRED_QUIRKS[q]}"
        for q in quirks
        if isinstance(q, str) and q in RETIRED_QUIRKS
    ]
