"""A monitor app over a controller that also serves the two pickers."""

from canlib.modes._monitor_selectors import PickerSeed
from canlib.modes._monitor_tui import (
    MonitorApp,
)
from canlib.modes.monitor_select import build_choices
from tests.support.monitor_tui import FakeController, FakeEditor


class PickerController(FakeController):
    """FakeController plus the registry surface the two pickers query."""

    def available_ecus(self):
        return ["BMS", "HVAC", "IGPM"]

    def picker_seed(self):
        def pid(**params):
            return {"parameters": params, "status": "active", "swept": True}

        index = {
            name: {"tx_id": 0x7E0 + i, "pids": {"2101": pid(pack={}), "2102": pid(cell={})}}
            for i, name in enumerate(("BMS", "HVAC", "IGPM"))
        }
        return PickerSeed(build_choices(index), [], {"BMS": ["2101"]}, False)

    def active_session_modes(self):
        return {}

    def known_session_types(self, ecu):
        return [("uds", "UDS", True), ("kwp", "KWP2000", False)]

    def apply_selection(self, selected):
        self.applied = selected
        return [f"{ecu}:{','.join(pids)}" for ecu, pids in selected.items()]


def monitor_app(**kw):
    kw.setdefault("editor", FakeEditor())
    return MonitorApp(PickerController(**kw))
