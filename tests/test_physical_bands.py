"""Tests for canlib.physical_bands — band resolution precedence."""

from __future__ import annotations

from canlib.physical_bands import DEFAULT_PHYSICAL_BANDS, resolve_physical_bands


def _as_dict(bands):
    """Map label -> (low, high) for convenient assertions (labels are unique in
    the default set)."""
    return {label: (lo, hi) for label, lo, hi in bands}


class TestResolveDefaults:
    def test_none_meta_returns_builtins(self):
        bands = resolve_physical_bands(None)
        assert bands == list(DEFAULT_PHYSICAL_BANDS.values())

    def test_empty_meta_returns_builtins(self):
        assert resolve_physical_bands({}) == list(DEFAULT_PHYSICAL_BANDS.values())

    def test_order_is_stable(self):
        labels = [b[0] for b in resolve_physical_bands({})]
        assert labels == [v[0] for v in DEFAULT_PHYSICAL_BANDS.values()]


class TestVehicleOverride:
    def test_override_existing_key_replaces_range(self):
        bands = _as_dict(resolve_physical_bands({"physical_bands": {"hv_pack": [450, 850]}}))
        assert bands["HV pack V"] == (450.0, 850.0)
        # Other built-ins unchanged.
        assert bands["12V rail V"] == (11.0, 15.0)

    def test_unknown_key_appended_as_custom_band(self):
        bands = resolve_physical_bands({"physical_bands": {"hv_pack_peak": [600, 900]}})
        labels = [b[0] for b in bands]
        # Custom band appended after the five built-ins, humanized label.
        assert labels[-1] == "hv pack peak"
        assert bands[-1] == ("hv pack peak", 600.0, 900.0)
        assert len(bands) == len(DEFAULT_PHYSICAL_BANDS) + 1

    def test_malformed_override_is_ignored(self):
        # Bad shapes are dropped (validation reports them separately).
        meta = {"physical_bands": {"hv_pack": [450], "rail_12v": "nope", "x": [5, 3]}}
        bands = _as_dict(resolve_physical_bands(meta))
        assert bands["HV pack V"] == (300.0, 450.0)  # unchanged
        assert bands["12V rail V"] == (11.0, 15.0)  # unchanged
        assert "x" not in {b[0] for b in resolve_physical_bands(meta)}


class TestGridPrecedence:
    def test_grid_region_replaces_grid_bands(self):
        bands = _as_dict(resolve_physical_bands({}, grid_region="US"))
        # US line-freq band is 60 Hz, replacing the EU-flavoured default.
        assert bands["line freq Hz"] == (59.0, 61.0)

    def test_grid_region_dual_bands(self):
        # US emits two mains_rms bands (120 V and 240 V).
        bands = resolve_physical_bands({}, grid_region="US")
        mains = [(lo, hi) for label, lo, hi in bands if label == "mains RMS V"]
        assert (108.0, 132.0) in mains
        assert (216.0, 264.0) in mains

    def test_profile_override_beats_grid_preset(self):
        # A profile pin on a grid band is the final say over the region preset.
        meta = {"physical_bands": {"line_freq": [40, 45]}}
        bands = _as_dict(resolve_physical_bands(meta, grid_region="US"))
        assert bands["line freq Hz"] == (40.0, 45.0)

    def test_unknown_region_falls_back_to_defaults(self):
        bands = resolve_physical_bands({}, grid_region="ZZ")
        assert bands == list(DEFAULT_PHYSICAL_BANDS.values())

    def test_none_region_keeps_defaults(self):
        assert resolve_physical_bands({}, grid_region=None) == list(DEFAULT_PHYSICAL_BANDS.values())
