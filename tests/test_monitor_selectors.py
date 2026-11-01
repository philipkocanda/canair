"""The live monitor's selection backend: swap the polled ECUs/PIDs mid-run."""

from __future__ import annotations

from types import SimpleNamespace

from canlib.modes._monitor_selectors import MonitorSelectors


class _Schedule:
    def __init__(self) -> None:
        self.woken = 0
        self.forgotten: list[tuple[str, str]] = []

    def wake(self) -> None:
        self.woken += 1

    def forget(self, ecu: str, pid: str) -> None:
        self.forgotten.append((ecu, pid))


class _State:
    def __init__(self) -> None:
        self.forgotten: list[tuple[str, str]] = []

    def forget_signal(self, ecu: str, pid: str) -> None:
        self.forgotten.append((ecu, pid))


def _pid(swept: bool = True) -> dict:
    return {"parameters": {"x": {}}, "status": "active" if swept else "slow", "swept": swept}


def _controller(steps: list[dict]) -> SimpleNamespace:
    index = {
        "BMS": {"tx_id": 0x7E4, "pids": {"2101": _pid(), "2105": _pid(), "21F2": _pid(False)}},
        "VCU": {"tx_id": 0x7E2, "pids": {"2101": _pid(), "2102": _pid()}},
    }
    return SimpleNamespace(
        _ecu_index=index,
        query_steps=steps,
        include_static=False,
        schedule=_Schedule(),
        state=_State(),
        _last_good={("BMS (0x7E4)", "2105", "x"): 1, ("VCU (0x7E2)", "2101", "x"): 2},
        _last_shown={("BMS (0x7E4)", "2105", "x"): 1},
    )


def _q(ecu: str, *pids: str) -> dict:
    return {"type": "query", "ecu": ecu, "pids": list(pids)}


class TestPickerSeed:
    def test_opens_on_the_polled_selection(self, monkeypatch):
        monkeypatch.setattr("canlib.ecus.ecu_groups.load_groups", lambda: {})
        seed = MonitorSelectors(_controller([_q("BMS"), _q("VCU", "2102")])).picker_seed()
        assert [c.name for c in seed.choices] == ["BMS", "VCU"]
        assert seed.current == {"BMS": ["2101", "2105"], "VCU": ["2102"]}


class TestApply:
    def test_narrows_a_sweep_and_forgets_what_it_drops(self):
        c = _controller([_q("BMS")])
        selectors = MonitorSelectors(c).apply({"BMS": ["2101"]})
        assert selectors == ["BMS:2101"]
        assert c.query_steps == [_q("BMS", "2101")]
        assert c.schedule.forgotten == [("BMS", "2105")]
        assert c.state.forgotten == [("BMS", "2105")]
        assert ("BMS (0x7E4)", "2105", "x") not in c._last_good
        assert c._last_shown == {}
        assert c.schedule.woken == 0

    def test_keeps_poll_order_and_appends_new_ecus(self):
        c = _controller([_q("VCU", "2102"), _q("BMS", "2101")])
        steps = c.query_steps
        MonitorSelectors(c).apply({"BMS": ["2101", "2105"], "VCU": ["2102"]})
        assert c.query_steps is steps  # mutated in place, as the poll loop holds it
        assert c.query_steps == [_q("VCU", "2102"), _q("BMS")]
        assert c.schedule.woken == 1

    def test_new_ecu_is_appended(self):
        c = _controller([_q("BMS", "2101")])
        MonitorSelectors(c).apply({"BMS": ["2101"], "VCU": ["2101", "2102"]})
        assert c.query_steps == [_q("BMS", "2101"), _q("VCU")]

    def test_steps_the_picker_cannot_show_survive(self):
        c = _controller([_q("SKM"), _q("BMS", "2101", "22BEEF")])
        MonitorSelectors(c).apply({"BMS": ["2101", "2105"]})
        assert c.query_steps == [_q("SKM"), _q("BMS", "2101", "2105", "22BEEF")]

    def test_dropping_an_ecu_forgets_all_its_pids(self):
        c = _controller([_q("BMS"), _q("VCU", "2101")])
        MonitorSelectors(c).apply({"VCU": ["2101"]})
        assert c.query_steps == [_q("VCU", "2101")]
        assert sorted(c.schedule.forgotten) == [("BMS", "2101"), ("BMS", "2105")]
