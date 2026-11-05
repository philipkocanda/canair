"""Validation of ``profile.yaml``: the required fields, types and the settings blocks."""

from pathlib import Path
from typing import Any

import yaml

from canair.core import yaml_io

from ._common import duplicate_key_errors, is_int
from .addressing import validate_addressing


def validate_meta(path: Path, required_fields: set[str]) -> list[str]:
    """Validate profile.yaml (profile-wide settings).

    Checks required fields are present and that every known field has the right
    type (and, for bounded values, a sane range). Unknown top-level keys are
    tolerated (profile.yaml stays extensible), but the ``isotp:`` block's keys
    are checked against the accepted set so a typo is caught.
    """
    from canair.transport.isotp_params import ISOTP_FIELDS

    errors = []
    try:
        text = path.read_text()
        data = yaml_io.safe_load(text)
    except yaml.YAMLError as e:
        return [f"profile.yaml: YAML parse error: {e}"]

    if not data or not isinstance(data, dict):
        return ["profile.yaml: empty or invalid"]

    errors += [f"profile.yaml: {e}" for e in duplicate_key_errors(text)]
    for field in required_fields:
        if field not in data:
            errors.append(f"profile.yaml: missing required field '{field}'")

    for field in ("car_model", "init"):
        if field in data and not isinstance(data[field], str):
            errors.append(f"profile.yaml: '{field}' must be a string")

    if "response_timeout_ms" in data and not (
        is_int(data["response_timeout_ms"]) and data["response_timeout_ms"] > 0
    ):
        errors.append("profile.yaml: 'response_timeout_ms' must be a positive integer")

    if "can_bitrate" in data and not (is_int(data["can_bitrate"]) and data["can_bitrate"] > 0):
        errors.append("profile.yaml: 'can_bitrate' must be a positive integer (bit/s)")

    if "addressing" in data:
        errors.extend(validate_addressing(data["addressing"]))

    if "multi_did_batching" in data and not isinstance(data["multi_did_batching"], bool):
        errors.append("profile.yaml: 'multi_did_batching' must be a boolean")

    if "multi_did_max" in data and (not is_int(data["multi_did_max"]) or data["multi_did_max"] < 1):
        errors.append("profile.yaml: 'multi_did_max' must be a positive integer")

    if "isotp" in data:
        errors.extend(_validate_isotp(data["isotp"], set(ISOTP_FIELDS)))

    if "quirks" in data:
        errors.extend(_validate_quirks(data["quirks"]))

    if "physical_bands" in data:
        errors.extend(_validate_physical_bands(data["physical_bands"]))

    if "unit_guess_candidates" in data:
        errors.extend(_validate_unit_guess_candidates(data["unit_guess_candidates"]))

    return errors


def _validate_unit_guess_candidates(candidates: Any) -> list[str]:
    """Validate the profile.yaml ``unit_guess_candidates:`` list.

    A list of mappings ``{factor, offset?, label, hint?, dimension?}``. Only the
    shape/types are checked — the resolver
    (``canair.analysis.physical.unit_guess.resolve_unit_candidates``) drops a malformed entry.
    """
    if not isinstance(candidates, list):
        return ["profile.yaml: 'unit_guess_candidates' must be a list of {factor, label, …}"]
    errors: list[str] = []
    for i, entry in enumerate(candidates):
        label = f"profile.yaml: unit_guess_candidates[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be a mapping")
            continue
        factor = entry.get("factor")
        if isinstance(factor, bool) or not isinstance(factor, (int, float)):
            errors.append(f"{label}: 'factor' must be a number")
        offset = entry.get("offset", 0.0)
        if isinstance(offset, bool) or not isinstance(offset, (int, float)):
            errors.append(f"{label}: 'offset' must be a number")
        if not isinstance(entry.get("label"), str) or not str(entry.get("label")).strip():
            errors.append(f"{label}: 'label' must be a non-empty string")
    return errors


def _validate_physical_bands(bands: Any) -> list[str]:
    """Validate the profile.yaml ``physical_bands:`` block.

    A mapping of band key → 2-element numeric ``[low, high]`` with ``low < high``.
    Unknown band keys are allowed (custom bands); only the shape is checked.
    """
    if not isinstance(bands, dict):
        return ["profile.yaml: 'physical_bands' must be a mapping of {name: [low, high]}"]
    errors: list[str] = []
    for name, rng in bands.items():
        label = f"profile.yaml: physical_bands.{name}"
        if not isinstance(rng, (list, tuple)) or len(rng) != 2:
            errors.append(f"{label} must be a 2-element list [low, high]")
            continue
        lo, hi = rng
        nums_ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lo, hi))
        if not nums_ok:
            errors.append(f"{label}: low and high must be numbers")
            continue
        if not lo < hi:
            errors.append(f"{label}: low ({lo}) must be less than high ({hi})")
    return errors


def _validate_quirks(quirks: Any) -> list[str]:
    """Validate the profile.yaml ``quirks:`` list against the known vocabulary."""
    from canair.safety.quirks import KNOWN_QUIRKS, RETIRED_QUIRKS

    if not isinstance(quirks, list):
        return ["profile.yaml: 'quirks' must be a list of quirk tokens"]
    errors: list[str] = []
    for q in quirks:
        if q not in KNOWN_QUIRKS and q not in RETIRED_QUIRKS:
            errors.append(
                f"profile.yaml: unknown quirk '{q}' (allowed: {', '.join(sorted(KNOWN_QUIRKS))})"
            )
    return errors


def retired_quirk_warnings(path: Path) -> list[str]:
    """A warning per retired quirk the profile.yaml still lists (see ``RETIRED_QUIRKS``)."""
    from canair.safety.quirks import RETIRED_QUIRKS

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


def _validate_isotp(isotp: Any, allowed: set[str]) -> list[str]:
    """Validate the profile.yaml ``isotp:`` block (client-side ISO-TP tuning)."""
    if not isinstance(isotp, dict):
        return ["profile.yaml: 'isotp' must be a mapping"]
    errors: list[str] = []
    for key in isotp:
        if key not in allowed:
            errors.append(
                f"profile.yaml: unknown isotp field '{key}' (allowed: {', '.join(sorted(allowed))})"
            )
    if "can_fd" in isotp and not isinstance(isotp["can_fd"], bool):
        errors.append("profile.yaml: 'isotp.can_fd' must be a boolean")
    for key in ("tx_padding", "rx_padding"):
        if key in isotp and not (is_int(isotp[key]) and 0 <= isotp[key] <= 255):
            errors.append(f"profile.yaml: 'isotp.{key}' must be a byte (0–255)")
    for key in ("blocksize", "stmin", "rx_flowcontrol_timeout", "rx_consecutive_frame_timeout"):
        if key in isotp and not (is_int(isotp[key]) and isotp[key] >= 0):
            errors.append(f"profile.yaml: 'isotp.{key}' must be a non-negative integer")
    if "tx_data_length" in isotp and not (
        is_int(isotp["tx_data_length"]) and isotp["tx_data_length"] > 0
    ):
        errors.append("profile.yaml: 'isotp.tx_data_length' must be a positive integer")
    return errors
