"""Tests for canlib.modes.monitor_edit — selection, filtering, in-place editing."""

import textwrap

import pytest
import yaml

from canlib.modes.monitor_edit import MonitorEditor


class FakeController:
    """Minimal stand-in for MonitorController for editor tests."""

    def __init__(self, pids_data, last_queries, pids_dir=None):
        self.pids_data = pids_data
        self.last_queries = last_queries
        self.pids_dir = pids_dir
        self.reloaded = 0

    def reload_pids(self):
        from canlib.pids import load_pids

        self.reloaded += 1
        if self.pids_dir is not None:
            self.pids_data = load_pids(self.pids_dir)


def _row(name, value=1.0, unit="", expr="B0", verified=False):
    # (name, value, unit, expression, error, verified, display)
    return (name, value, unit, expr, None, verified, "")


def _pids_data(params):
    """Wrap a {param_name: pdef} map into a pids_data dict for BMS 2101."""
    return {"ecus": {"BMS": {"tx_id": 0x7E4, "pids": {"2101": {"parameters": params}}}}}


# ── filtering ────────────────────────────────────────────────────────────────
class TestFilter:
    def _editor(self):
        params = {
            "SOC": {"expression": "B0", "verified": True, "enabled": True},
            "TEMP": {"expression": "B1", "verified": False, "enabled": True},
            "GUESS": {"expression": "B2", "verified": False, "enabled": False},
        }
        last_queries = [
            (
                "BMS (0x7E4)",
                [
                    {
                        "pid": "2101",
                        "params": [
                            _row("SOC", verified=True),
                            _row("TEMP", verified=False),
                            _row("GUESS", verified=False),
                        ],
                        "raw_hex": "6101ABCD",
                    }
                ],
            )
        ]
        return MonitorEditor(FakeController(_pids_data(params), last_queries))

    def test_all_passthrough(self):
        ed = self._editor()
        assert ed.filter_mode == "all"
        vis = ed.visible_queries(ed.c.last_queries)
        assert vis is ed.c.last_queries  # untouched

    def test_verified_filter(self):
        ed = self._editor()
        ed.filter_mode = "verified"
        names = [
            r[0]
            for _, entries in ed.visible_queries(ed.c.last_queries)
            for e in entries
            for r in e["params"]
        ]
        assert names == ["SOC"]

    def test_unverified_filter(self):
        ed = self._editor()
        ed.filter_mode = "unverified"
        names = [
            r[0]
            for _, entries in ed.visible_queries(ed.c.last_queries)
            for e in entries
            for r in e["params"]
        ]
        assert names == ["TEMP", "GUESS"]

    def test_disabled_filter(self):
        ed = self._editor()
        ed.filter_mode = "disabled"
        names = [
            r[0]
            for _, entries in ed.visible_queries(ed.c.last_queries)
            for e in entries
            for r in e["params"]
        ]
        assert names == ["GUESS"]

    def test_enabled_filter(self):
        ed = self._editor()
        ed.filter_mode = "enabled"
        names = [
            r[0]
            for _, entries in ed.visible_queries(ed.c.last_queries)
            for e in entries
            for r in e["params"]
        ]
        assert names == ["SOC", "TEMP"]

    def test_cycle_filter(self):
        ed = self._editor()
        assert ed.cycle_filter(ed.c.last_queries) == "verified"
        assert ed.cycle_filter(ed.c.last_queries) == "unverified"

    def test_filter_hides_raw_only_entries(self):
        # A PID with no params (raw/unmapped) is dropped under any non-all filter.
        lq = [("VCU (0x7E2)", [{"pid": "2102", "params": [], "raw_hex": "6202FF"}])]
        ed = MonitorEditor(FakeController(_pids_data({}), lq))
        ed.filter_mode = "verified"
        assert ed.visible_queries(lq) == []


# ── selection ────────────────────────────────────────────────────────────────
class TestSelection:
    def _editor(self):
        lq = [
            ("BMS (0x7E4)", [{"pid": "2101", "params": [_row("SOC"), _row("TEMP")]}]),
            ("VCU (0x7E2)", [{"pid": "2102", "params": [_row("SPEED")]}]),
        ]
        return MonitorEditor(FakeController(_pids_data({}), lq))

    def test_first_move_snaps_to_first(self):
        ed = self._editor()
        assert ed.move(ed.c.last_queries, 1) == ("BMS (0x7E4)", "2101", "SOC")

    def test_first_move_up_snaps_to_last(self):
        ed = self._editor()
        assert ed.move(ed.c.last_queries, -1) == ("VCU (0x7E2)", "2102", "SPEED")

    def test_move_forward_and_clamp(self):
        ed = self._editor()
        ed.move(ed.c.last_queries, 1)  # SOC
        assert ed.move(ed.c.last_queries, 1) == ("BMS (0x7E4)", "2101", "TEMP")
        assert ed.move(ed.c.last_queries, 1) == ("VCU (0x7E2)", "2102", "SPEED")
        # Clamp at the end.
        assert ed.move(ed.c.last_queries, 1) == ("VCU (0x7E2)", "2102", "SPEED")

    def test_ensure_valid_drops_vanished_selection(self):
        ed = self._editor()
        ed.selected = ("BMS (0x7E4)", "2101", "GONE")
        ed.ensure_valid(ed.c.last_queries)
        assert ed.selected is None

    def test_selection_label(self):
        ed = self._editor()
        ed.move(ed.c.last_queries, 1)
        assert ed.selection_label() == "BMS 2101 SOC"


# ── editing (writes to disk) ──────────────────────────────────────────────────
@pytest.fixture
def ecus_dir(tmp_path):
    (tmp_path / "bms.yaml").write_text(
        textwrap.dedent(
            """\
            BMS:
              tx_id: 0x7E4
              pids:
                2101:
                  enabled: true
                  parameters:
                    SOC:
                      expression: "B4/2"
                      unit: "%"
                      verified: false
                      enabled: true
            """
        )
    )
    return tmp_path


def _editor_on_disk(ecus_dir):
    from canlib.pids import load_pids

    pids_data = load_pids(ecus_dir)
    lq = [("BMS (0x7E4)", [{"pid": "2101", "params": [_row("SOC", expr="B4/2")]}])]
    ed = MonitorEditor(FakeController(pids_data, lq, pids_dir=ecus_dir))
    ed.move(lq, 1)  # select SOC
    return ed


def _saved_soc(ecus_dir):
    data = yaml.safe_load((ecus_dir / "bms.yaml").read_text())
    return data["BMS"]["pids"][2101]["parameters"]["SOC"]


class TestEditTarget:
    def test_edit_target_prefill(self, ecus_dir):
        ed = _editor_on_disk(ecus_dir)
        t = ed.edit_target()
        assert t["ecu"] == "BMS" and t["pid"] == "2101" and t["name"] == "SOC"
        assert t["expression"] == "B4/2"
        assert t["unit"] == "%"
        assert t["verified"] is False
        assert t["enabled"] is True

    def test_edit_target_none_when_unselected(self, ecus_dir):
        ed = _editor_on_disk(ecus_dir)
        ed.selected = None
        assert ed.edit_target() is None


class TestApplyEdit:
    def test_apply_edit_changes_expression_and_flags(self, ecus_dir):
        ed = _editor_on_disk(ecus_dir)
        msg = ed.apply_edit(
            {
                "expression": "B4",
                "unit": "%",
                "min": "0",
                "max": "100",
                "notes": "recalibrated",
                "verified": True,
                "enabled": True,
            }
        )
        assert "Saved" in msg
        assert ed.c.reloaded == 1
        soc = _saved_soc(ecus_dir)
        assert soc["expression"] == "B4"
        assert soc["verified"] is True
        assert soc["min"] == "0"
        assert "recalibrated" in soc["notes"]

    def test_apply_edit_empty_optional_preserves(self, ecus_dir):
        ed = _editor_on_disk(ecus_dir)
        ed.apply_edit(
            {
                "expression": "B4/2",
                "unit": "",
                "min": "",
                "max": "",
                "notes": "",
                "verified": False,
                "enabled": True,
            }
        )
        soc = _saved_soc(ecus_dir)
        assert soc["unit"] == "%"  # empty didn't clobber

    def test_apply_edit_empty_expression_fails(self, ecus_dir):
        ed = _editor_on_disk(ecus_dir)
        msg = ed.apply_edit({"expression": "  ", "verified": True})
        assert "failed" in msg.lower()
        assert _saved_soc(ecus_dir)["expression"] == "B4/2"  # unchanged

    def test_apply_edit_no_selection(self, ecus_dir):
        ed = _editor_on_disk(ecus_dir)
        ed.selected = None
        assert "No parameter selected" in ed.apply_edit({"expression": "B4"})


class TestToggles:
    def test_toggle_verified(self, ecus_dir):
        ed = _editor_on_disk(ecus_dir)
        assert "verified=true" in ed.toggle_verified()
        assert _saved_soc(ecus_dir)["verified"] is True
        # Reload propagated into pids_data, so a second toggle flips back.
        assert "verified=false" in ed.toggle_verified()
        assert _saved_soc(ecus_dir)["verified"] is False

    def test_toggle_enabled(self, ecus_dir):
        ed = _editor_on_disk(ecus_dir)
        assert "enabled=false" in ed.toggle_enabled()
        assert _saved_soc(ecus_dir)["enabled"] is False

    def test_toggle_no_selection(self, ecus_dir):
        ed = _editor_on_disk(ecus_dir)
        ed.selected = None
        assert "No parameter selected" in ed.toggle_verified()
