"""Regional charging-grid presets for the physical-band plausibility scan.

The mains-voltage and line-frequency bands the physical scan looks for depend on
*where the car charges*, not on the car itself — the same EV charges from
230 V / 50 Hz in Berlin and split-phase 120/240 V / 60 Hz in Denver. So the grid
bands come from a user-config ``grid_region`` preset (set once per location),
not from the vehicle profile. See :mod:`canlib.physical_bands` for the two-axis
design and the precedence rules.

RMS ranges are nominal ±~10 %; peak = RMS·√2 with a similar margin; line
frequency ±1 Hz. A region absent here is a one-line addition to
:data:`GRID_PRESETS` (or the user pins explicit bands in ``profile.yaml``).
"""

from __future__ import annotations

from .physical_bands import _label_for

# Region -> {grid band key -> list of (low, high)}. A region may emit more than
# one band per key (US Level 1 120 V *and* Level 2 240 V; Japan's dual 50/60 Hz).
_PRESET_RANGES: dict[str, dict[str, list[tuple[float, float]]]] = {
    "EU": {  # 230 V / 50 Hz
        "mains_rms": [(207.0, 253.0)],
        "mains_peak": [(290.0, 360.0)],
        "line_freq": [(49.0, 51.0)],
    },
    "UK": {  # 230 V / 50 Hz (240 V legacy nominal)
        "mains_rms": [(216.0, 260.0)],
        "mains_peak": [(300.0, 370.0)],
        "line_freq": [(49.0, 51.0)],
    },
    "AU": {  # 230 V / 50 Hz
        "mains_rms": [(207.0, 253.0)],
        "mains_peak": [(290.0, 360.0)],
        "line_freq": [(49.0, 51.0)],
    },
    "CN": {  # 220 V / 50 Hz
        "mains_rms": [(198.0, 242.0)],
        "mains_peak": [(280.0, 345.0)],
        "line_freq": [(49.0, 51.0)],
    },
    "US": {  # 120/240 V split-phase / 60 Hz
        "mains_rms": [(108.0, 132.0), (216.0, 264.0)],
        "mains_peak": [(150.0, 190.0), (300.0, 375.0)],
        "line_freq": [(59.0, 61.0)],
    },
    "JP": {  # 100/200 V / 50 Hz (east) + 60 Hz (west)
        "mains_rms": [(90.0, 110.0), (190.0, 220.0)],
        "mains_peak": [(130.0, 160.0), (270.0, 315.0)],
        "line_freq": [(49.0, 51.0), (59.0, 61.0)],
    },
}

# The region tokens accepted by `config set grid_region` / --help.
GRID_REGIONS: tuple[str, ...] = tuple(_PRESET_RANGES)


def resolve_grid_bands(region: str | None) -> dict[str, list[tuple[str, float, float]]]:
    """Expand a region token into ``{grid_key: [(label, low, high), ...]}``.

    Case-insensitive. Returns an empty mapping for ``None`` or an unrecognized
    region, which leaves the built-in (EU-flavoured) grid defaults in place.
    """
    if not region:
        return {}
    ranges = _PRESET_RANGES.get(region.strip().upper())
    if ranges is None:
        return {}
    return {key: [(_label_for(key), lo, hi) for lo, hi in pairs] for key, pairs in ranges.items()}
