"""Unit tests for the monitor's per-signal value-range accumulator.

Covers :class:`canlib.modes._monitor_stats.ParamStats` (numeric min/max, distinct
non-numeric labels, bool-as-flag handling, overflow) and the
:func:`canlib.formatting.render_param_ranges` renderer that presents it.
"""

from canlib.formatting import render_param_ranges
from canlib.modes._monitor_stats import ParamStats


def _row(name, value, unit="", verified=True):
    return (name, value, unit, "B09", None, verified, "")


class TestParamStats:
    def test_numeric_min_max(self):
        stats = ParamStats()
        key = ("BMS", "2101")
        for v in (50.0, 62.5, 41.0, 55.0):
            stats.observe(key, [_row("SOC", v, "%")])
        s = stats.for_pid(key)["SOC"]
        assert s["min"] == 41.0
        assert s["max"] == 62.5
        assert s["n"] == 4
        assert s["unit"] == "%"
        assert s["verified"] is True

    def test_distinct_non_numeric(self):
        stats = ParamStats()
        key = ("HVAC", "220102")
        for v in ("off", "fan1", "fanMAX", "fan1", "off"):
            stats.observe(key, [_row("MODE", v)])
        s = stats.for_pid(key)["MODE"]
        assert s["min"] is None and s["max"] is None
        assert s["values"] == ["off", "fan1", "fanMAX"]
        assert s["n"] == 5

    def test_bool_treated_as_flag_not_number(self):
        stats = ParamStats()
        key = ("IGPM", "BC03")
        stats.observe(key, [_row("DOOR", True)])
        stats.observe(key, [_row("DOOR", False)])
        s = stats.for_pid(key)["DOOR"]
        # A bool must not collapse into a 0..1 numeric range.
        assert s["min"] is None and s["max"] is None
        assert set(s["values"]) == {"true", "false"}

    def test_error_rows_skipped(self):
        stats = ParamStats()
        key = ("BMS", "2101")
        stats.observe(key, [("BAD", None, "", "B99", "boom", False, "")])
        assert stats.for_pid(key) == {}

    def test_overflow_capped(self):
        stats = ParamStats()
        key = ("X", "1")
        for i in range(30):
            stats.observe(key, [_row("LABEL", f"v{i}")])
        s = stats.for_pid(key)["LABEL"]
        assert len(s["values"]) == 12  # _MAX_DISTINCT
        assert s["overflow"] == 30 - 12

    def test_unknown_pid_returns_empty(self):
        assert ParamStats().for_pid(("NOPE", "0000")) == {}


class TestRenderParamRanges:
    def test_empty_stats(self):
        assert render_param_ranges({}).plain == ""

    def test_numeric_range_rendered(self):
        stats = {
            "SOC": {
                "unit": "%",
                "verified": True,
                "n": 4,
                "min": 41.0,
                "max": 62.5,
                "values": [],
                "overflow": 0,
            }
        }
        text = render_param_ranges(stats).plain
        assert "SOC" in text
        assert "41 – 62.50 %" in text
        assert "n=4" in text
        assert "✓" in text

    def test_constant_shown_as_single_value(self):
        stats = {
            "TEMP": {
                "unit": "°C",
                "verified": False,
                "n": 3,
                "min": 40.0,
                "max": 40.0,
                "values": [],
                "overflow": 0,
            }
        }
        text = render_param_ranges(stats).plain
        assert "40 °C" in text
        assert "–" not in text  # no range dash for a constant
        assert "?" in text  # unverified mark

    def test_distinct_values_with_overflow(self):
        stats = {
            "MODE": {
                "unit": "",
                "verified": True,
                "n": 20,
                "min": None,
                "max": None,
                "values": ["a", "b", "c", "d", "e", "f", "g"],
                "overflow": 2,
            }
        }
        text = render_param_ranges(stats).plain
        assert "a, b, c, d, e, f" in text
        # 1 beyond the 6 shown + 2 overflow = 3 more.
        assert "(+3 more)" in text

    def test_selected_row_marked(self):
        stats = {
            "SOC": {
                "unit": "%",
                "verified": True,
                "n": 1,
                "min": 1.0,
                "max": 2.0,
                "values": [],
                "overflow": 0,
            }
        }
        text = render_param_ranges(stats, selected_name="SOC").plain
        assert "▶ SOC" in text
