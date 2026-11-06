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
  ``grid_region`` preset (see :mod:`canair.analysis.physical.grid_regions`).

Precedence, first match wins: ``profile.yaml physical_bands.<key>`` (final say)
→ ``grid_region`` preset (grid bands only) → built-in default.
"""

from __future__ import annotations

from typing import Any

from canair.analysis.physical.default_bands import DEFAULT_PHYSICAL_BANDS, band_label
from canair.analysis.physical.grid_regions import resolve_grid_bands

# The three bands that belong to the grid axis (owned by grid_region, not the
# profile). Everything else is a vehicle-axis band.
GRID_KEYS: tuple[str, ...] = ("mains_rms", "mains_peak", "line_freq")


def _parse_range(rng: Any) -> tuple[float, float] | None:
    """Parse a ``[low, high]`` override into a validated float pair, or None.

    Lenient by design: malformed overrides are dropped here and reported
    separately by ``canair validate`` (``profile_schema.json`` and
    ``validation/meta.py``) — the resolver never raises on bad profile data.
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
            bands[str(key)] = [(band_label(str(key)), lo, hi)]

    return [band for band_list in bands.values() for band in band_list]
