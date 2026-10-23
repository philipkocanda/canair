"""The bare ``canair monitor`` picker: which ECUs, then which PIDs."""

from __future__ import annotations

import pytest
from textual.widgets import Input, SelectionList

from canlib.ecu_groups import Group
from canlib.modes._monitor_select_tui import MonitorSelectApp
from canlib.modes.monitor_select import (
    build_choices,
    build_group_choices,
    pick_selectors,
    selectors_from_selection,
)


def _index() -> dict:
    def pid(status: str = "active", swept: bool = True, **params: object) -> dict:
        return {"parameters": params, "status": status, "swept": swept}

    return {
        "VCU": {"tx_id": 0x7E2, "pids": {"2101": pid(rpm={}), "2102": pid(speed={})}},
        "BMS": {
            "tx_id": 0x7E4,
            "pids": {
                "2101": pid(soc={}, volts={}),
                "2105": pid(),
                "21F2": pid("slow", False, cycle={}),
            },
        },
        "AMP": {"tx_id": 0x7D0, "pids": {}},
    }


class TestChoices:
    def test_sorted_and_identity_only_ecus_dropped(self):
        assert [c.name for c in build_choices(_index())] == ["BMS", "VCU"]

    def test_default_pids_follow_the_bare_sweep(self):
        bms = build_choices(_index())[0]
        assert bms.default_pids(include_static=False) == ["2101", "2105"]
        assert bms.default_pids(include_static=True) == ["2101", "2105", "21F2"]

    def test_signal_names_are_joined(self):
        bms = build_choices(_index())[0]
        assert bms.pids[0].signals == "soc, volts"


class TestSelectors:
    def test_default_sweep_collapses_to_the_bare_ecu(self):
        choices = build_choices(_index())
        assert selectors_from_selection(choices, {"BMS": ["2101", "2105"]}) == ["BMS"]

    def test_include_static_changes_what_counts_as_the_sweep(self):
        choices = build_choices(_index())
        picked = {"BMS": ["2101", "2105", "21F2"]}
        assert selectors_from_selection(choices, picked, include_static=True) == ["BMS"]
        assert selectors_from_selection(choices, picked) == ["BMS:2101,2105,21F2"]

    def test_subset_is_spelled_out_in_display_order(self):
        choices = build_choices(_index())
        assert selectors_from_selection(choices, {"VCU": ["2102", "2101"]}) == ["VCU"]
        assert selectors_from_selection(choices, {"VCU": ["2102"]}) == ["VCU:2102"]

    def test_follows_choice_order_and_skips_empty(self):
        choices = build_choices(_index())
        picked = {"VCU": ["2101"], "BMS": ["2105"], "AMP": []}
        assert selectors_from_selection(choices, picked) == ["BMS:2105", "VCU:2101"]


class TestGroups:
    def _groups(self, *groups: Group) -> list:
        return build_group_choices({g.name: g for g in groups}, build_choices(_index()))

    def test_members_resolve_to_pids_and_bare_ecus_to_their_sweep(self):
        (g,) = self._groups(Group("charging", "plugged in", ("BMS:2105", "VCU")))
        assert g.picks == {"BMS": ("2105",), "VCU": ("2101", "2102")}
        assert g.skipped == ()

    def test_unrepresentable_members_are_reported_not_dropped_silently(self):
        (g,) = self._groups(Group("odd", "", ("BMS:2101", "NOPE", "VCU:9999", "AMP")))
        assert g.picks == {"BMS": ("2101",)}
        assert set(g.skipped) == {"NOPE", "VCU:9999", "AMP"}

    def test_group_with_nothing_pickable_is_hidden(self):
        assert self._groups(Group("empty", "", ("AMP", "NOPE"))) == []


class TestPickSelectors:
    def test_empty_profile_is_an_error(self):
        with pytest.raises(ValueError, match="nothing to pick"):
            pick_selectors({"ecus": {}})


async def _press(pilot, *keys: str) -> None:
    for key in keys:
        await pilot.press(key)
    await pilot.pause()


class TestApp:
    def _app(self, include_static: bool = False) -> MonitorSelectApp:
        return MonitorSelectApp(build_choices(_index()), include_static=include_static)

    @pytest.mark.asyncio
    async def test_pick_ecu_then_accept_default_pids(self):
        app = self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await _press(pilot, "space", "ctrl+s")  # tick BMS, next
            assert app._step == 2
            await _press(pilot, "ctrl+s")
        assert app.return_value == {"BMS": ["2101", "2105"]}

    @pytest.mark.asyncio
    async def test_untick_a_pid_in_step_two(self):
        app = self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await _press(pilot, "space", "ctrl+s", "space", "ctrl+s")  # drop BMS 2101
        assert app.return_value == {"BMS": ["2105"]}

    @pytest.mark.asyncio
    async def test_next_without_an_ecu_stays_on_step_one(self):
        app = self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await _press(pilot, "ctrl+s")
            assert app._step == 1
            await _press(pilot, "q")
        assert app.return_value is None

    @pytest.mark.asyncio
    async def test_escape_goes_back_and_keeps_edits(self):
        app = self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await _press(pilot, "space", "ctrl+s", "space", "escape")  # drop 2101, back
            assert app._step == 1
            await _press(pilot, "ctrl+s", "ctrl+s")
        assert app.return_value == {"BMS": ["2105"]}

    @pytest.mark.asyncio
    async def test_filter_then_toggle_all_acts_on_visible_only(self):
        app = self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await _press(pilot, "slash", "v", "c", "u")
            assert isinstance(app.focused, Input)
            await _press(pilot, "escape", "ctrl+a")  # leave filter, tick visible (VCU)
            assert app._ecus == {"VCU"}
            await _press(pilot, "ctrl+s")
            assert list(app._pids) == ["VCU"]
            await _press(pilot, "ctrl+s")
        assert app.return_value == {"VCU": ["2101", "2102"]}

    def _grouped_app(self) -> MonitorSelectApp:
        choices = build_choices(_index())
        groups = build_group_choices(
            {"drive": Group("drive", "driving", ("BMS:2105", "VCU:2102"))}, choices
        )
        return MonitorSelectApp(choices, groups)

    @pytest.mark.asyncio
    async def test_group_ticks_its_ecus_and_seeds_its_pids(self):
        app = self._grouped_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await _press(pilot, "space")  # the @drive row is first
            assert app._effective_ecus() == {"BMS", "VCU"}
            await _press(pilot, "ctrl+s", "ctrl+s")
        assert app.return_value == {"BMS": ["2105"], "VCU": ["2102"]}

    @pytest.mark.asyncio
    async def test_group_plus_directly_ticked_ecu_combines_pids(self):
        app = self._grouped_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await _press(pilot, "down", "space", "up", "space")  # BMS row, then @drive
            await _press(pilot, "ctrl+s", "ctrl+s")
        assert app.return_value == {"BMS": ["2101", "2105"], "VCU": ["2102"]}

    @pytest.mark.asyncio
    async def test_unticking_a_group_releases_its_ecus(self):
        app = self._grouped_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await _press(pilot, "space", "space")
            assert app._effective_ecus() == set()

    @pytest.mark.asyncio
    async def test_ecu_unticked_by_hand_stays_out_of_a_ticked_group(self):
        app = self._grouped_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await _press(pilot, "space", "down", "space")  # @drive, then untick BMS
            assert app._effective_ecus() == {"VCU"}

    @pytest.mark.asyncio
    async def test_toggle_all_twice_clears(self):
        app = self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await _press(pilot, "ctrl+a")
            assert app._ecus == {"BMS", "VCU"}
            assert len(app.query_one(SelectionList).selected) == 2
            await _press(pilot, "ctrl+a")
            assert app._ecus == set()
