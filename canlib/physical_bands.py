"""Resolve the physical-value bands used by the reference-free plausibility scan.

The physical scan (``canair hunt --physical`` / ``canair investigate``) flags a
raw byte whose scaled value lands in a named physical range — HV pack volts,
mains RMS/peak, line frequency, the 12 V rail. Those ranges are **car-class and
grid-region assumptions**: a ~400 V EV on a 230 V / 50 Hz grid. Hardwiring them
silently fails to flag the real signal on an 800 V architecture or a non-EU grid.

Bands split across two independent axes with different owners:

- **Vehicle axis** (``hv_pack``, ``rail_12v``) — a fact about the *car model*,
  the same everywhere it's driven → overridden in ``profile.yaml``
  ``physical_bands:``.
- **Grid axis** (``mains_rms``, ``mains_peak``, ``line_freq``) — a fact about
  *where the car charges*, not the car → set once per user via the user-config
  ``grid_region`` preset (see :mod:`canlib.grid_regions`).

Precedence, first match wins: ``profile.yaml physical_bands.<key>`` (final say)
→ ``grid_region`` preset (grid bands only) → built-in default.
"""

from __future__ import annotations

from typing import Any

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

# The three bands that belong to the grid axis (owned by grid_region, not the
# profile). Everything else is a vehicle-axis band.
GRID_KEYS: tuple[str, ...] = ("mains_rms", "mains_peak", "line_freq")


def _label_for(key: str) -> str:
    """Display label for a band key — the built-in label, or a humanized key."""
    known = DEFAULT_PHYSICAL_BANDS.get(key)
    if known is not None:
        return known[0]
    return key.replace("_", " ")


def _parse_range(rng: Any) -> tuple[float, float] | None:
    """Parse a ``[low, high]`` override into a validated float pair, or None.

    Lenient by design: malformed overrides are dropped here and reported
    separately by ``canair validate`` (``_validate_physical_bands``) — the
    resolver never raises on bad profile data.
    """
    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        return None
    lo, hi = rng
    if isinstance(lo, bool) or isinstance(hi, bool):
        return None
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return None
    if not lo < hi:
        return None
    return float(lo), float(hi)


def resolve_physical_bands(
    meta: dict | None,
    *,
    grid_region: str | None = None,
) -> list[tuple[str, float, float]]:
    """Resolve the effective physical bands for a profile + grid region.

    Starts from :data:`DEFAULT_PHYSICAL_BANDS`, replaces the three grid bands
    with the ``grid_region`` preset (when set), then applies the profile's
    ``physical_bands:`` overrides (override a built-in by key, append unknown
    keys as custom bands). Returns the flat ``(label, low, high)`` list the scan
    consumes, preserving a stable order (built-ins first, custom bands appended).
    """
    # key -> list of bands (a grid region may emit >1 band per key, e.g. US 120/240 V).
    bands: dict[str, list[tuple[str, float, float]]] = {
        key: [value] for key, value in DEFAULT_PHYSICAL_BANDS.items()
    }

    if grid_region is not None:
        from .grid_regions import resolve_grid_bands

        preset = resolve_grid_bands(grid_region)
        for key in GRID_KEYS:
            if key in preset:
                bands[key] = preset[key]

    overrides = (meta or {}).get("physical_bands")
    if isinstance(overrides, dict):
        for key, rng in overrides.items():
            parsed = _parse_range(rng)
            if parsed is None:
                continue
            lo, hi = parsed
            # Assigning replaces a built-in in place, or appends a custom key
            # at the end (dicts preserve insertion order).
            bands[str(key)] = [(_label_for(str(key)), lo, hi)]

    return [band for band_list in bands.values() for band in band_list]
