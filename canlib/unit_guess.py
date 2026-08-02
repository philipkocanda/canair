"""Physical-unit-guess candidate scalings for the byte/hunt unit sniffer.

``canair hunt`` / ``investigate`` guess a raw byte's physical scaling by fitting
each candidate ``physical = raw*factor + offset`` against a known reference and
picking the closest. The candidate list used to bake a Hyundai/Kia-specific
``raw/2−40 "HK temp"`` entry into the shared tool; it is now make-neutral here,
and a profile can *extend* it (the same tool-neutral / profile-declares pattern
as :mod:`canlib.physical_bands`).

A candidate is ``(factor, offset, numeric_label, hint, dimension)``:

* ``numeric_label`` — the dimensionless scale, always shown (e.g. ``raw/2``).
* ``hint`` — an optional domain flavour (e.g. ``cell V``) shown only when a known
  reference unit's ``dimension`` agrees, so a speed reference never mislabels an
  RPM slope as a voltage. ``None`` for the plain scalings.
* ``dimension`` — coarse physical dimension (``voltage``/``temperature``/
  ``speed``) that gates the hint. ``None`` when there is nothing to gate.
"""

from __future__ import annotations

from typing import Any

UnitCandidate = tuple[float, float, str, str | None, str | None]

# Make-neutral built-in scalings. The temperature entry that was once labelled
# "HK temp" is a plain half-degree-C with -40 C offset encoding used by many
# makes, so it keeps a neutral numeric label. A profile adds any make-specific
# scalings via `unit_guess_candidates:` rather than editing this list.
DEFAULT_UNIT_CANDIDATES: list[UnitCandidate] = [
    (1.0, 0.0, "raw (×1)", None, None),
    (0.5, 0.0, "raw/2", None, None),
    (0.1, 0.0, "raw/10", None, None),
    (0.01, 0.0, "raw/100", None, None),
    (0.02, 0.0, "raw×0.02", "cell V", "voltage"),
    (1.0, -40.0, "raw−40", "°C offset", "temperature"),
    (0.5, -40.0, "raw/2−40", "½°C −40", "temperature"),
    (1.609344, 0.0, "raw×1.609", "mph→km/h", "speed"),
    (0.621371, 0.0, "raw×0.621", "km/h→mph", "speed"),
]


def _parse_candidate(entry: Any) -> UnitCandidate | None:
    """Parse one profile ``unit_guess_candidates:`` entry into a tuple, or None.

    Accepts a mapping ``{factor, offset?, label, hint?, dimension?}``. Lenient by
    design: a malformed entry is dropped here and reported separately by
    ``canair validate`` — the resolver never raises on bad profile data.
    """
    if not isinstance(entry, dict):
        return None
    factor = entry.get("factor")
    label = entry.get("label")
    if isinstance(factor, bool) or not isinstance(factor, (int, float)):
        return None
    if not isinstance(label, str) or not label.strip():
        return None
    offset = entry.get("offset", 0.0)
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        return None
    hint = entry.get("hint")
    dimension = entry.get("dimension")
    hint = hint if isinstance(hint, str) and hint.strip() else None
    dimension = dimension if isinstance(dimension, str) and dimension.strip() else None
    return (float(factor), float(offset), label.strip(), hint, dimension)


def resolve_unit_candidates(meta: dict | None) -> list[UnitCandidate]:
    """The effective unit-guess candidates for a profile.

    Starts from :data:`DEFAULT_UNIT_CANDIDATES` and appends any well-formed
    entries from the profile's ``unit_guess_candidates:`` list. Order is
    stable (built-ins first, profile candidates appended).
    """
    candidates = list(DEFAULT_UNIT_CANDIDATES)
    extra = (meta or {}).get("unit_guess_candidates")
    if isinstance(extra, list):
        for entry in extra:
            parsed = _parse_candidate(entry)
            if parsed is not None:
                candidates.append(parsed)
    return candidates
