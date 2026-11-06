"""The built-in physical bands and their display labels.

A leaf of :mod:`canair.analysis.physical.bands`: the grid presets
(:mod:`canair.analysis.physical.grid_regions`) label their bands from this table, and the
resolver in ``bands`` imports the presets, so both import this instead of each other.
"""

from __future__ import annotations

# Canonical built-in bands keyed by a stable identifier decoupled from the
# display label. Insertion order matches the historical PHYSICAL_BANDS list so
# the default (no-override) output is byte-identical to the pre-config behaviour.
DEFAULT_PHYSICAL_BANDS: dict[str, tuple[str, float, float]] = {
    "mains_rms": ("mains RMS V", 200.0, 250.0),
    "mains_peak": ("mains peak V", 300.0, 340.0),
    "line_freq": ("line freq Hz", 49.0, 51.0),
    "rail_12v": ("12V rail V", 11.0, 15.0),
    "hv_pack": ("HV pack V", 300.0, 450.0),
    "temp_c": ("temp °C", -30.0, 130.0),
}


def band_label(key: str) -> str:
    """Display label for a band key — the built-in label, or a humanized key."""
    known = DEFAULT_PHYSICAL_BANDS.get(key)
    if known is not None:
        return known[0]
    return key.replace("_", " ")
