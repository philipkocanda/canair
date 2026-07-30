"""Tests for canlib.grid_regions — regional charging-grid presets."""

from __future__ import annotations

import pytest

from canlib.grid_regions import GRID_REGIONS, resolve_grid_bands


class TestPresetShape:
    @pytest.mark.parametrize("region", GRID_REGIONS)
    def test_each_preset_has_three_grid_keys(self, region):
        preset = resolve_grid_bands(region)
        assert set(preset) == {"mains_rms", "mains_peak", "line_freq"}
        for bands in preset.values():
            assert bands, "each grid key must emit at least one band"
            for label, lo, hi in bands:
                assert isinstance(label, str) and label
                assert lo < hi

    def test_case_insensitive(self):
        assert resolve_grid_bands("us") == resolve_grid_bands("US")
        assert resolve_grid_bands("  Eu ") == resolve_grid_bands("EU")


class TestDualBands:
    def test_us_has_two_mains_bands(self):
        preset = resolve_grid_bands("US")
        assert len(preset["mains_rms"]) == 2
        assert len(preset["mains_peak"]) == 2
        assert preset["line_freq"][0] == ("line freq Hz", 59.0, 61.0)

    def test_jp_dual_frequency(self):
        preset = resolve_grid_bands("JP")
        freqs = [(lo, hi) for _, lo, hi in preset["line_freq"]]
        assert (49.0, 51.0) in freqs
        assert (59.0, 61.0) in freqs


class TestUnknownAndNone:
    def test_none_returns_empty(self):
        assert resolve_grid_bands(None) == {}

    def test_empty_string_returns_empty(self):
        assert resolve_grid_bands("") == {}

    def test_unknown_region_returns_empty(self):
        assert resolve_grid_bands("ZZ") == {}
