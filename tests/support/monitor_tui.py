"""Fake controller and editor for the monitor TUI tests."""

from typing import ClassVar

from rich.text import Text


class FakeController:
    """Stand-in for MonitorController: canned render + counting poll."""

    def __init__(
        self,
        *,
        keep_mode=None,
        n_lines=50,
        disconnect_after=None,
        has_captures=True,
        query_label="BMS:2101",
        editor=None,
        journal=None,
    ):
        self.cycle = 0
        self.elapsed = 0.0
        from canair.live.poll_schedule import PollScheduler

        self.schedule = PollScheduler("fixed", 0.05)
        self.last_cmds = 0
        self.last_elm_time = 0.0
        self.total_frames = 0
        self.unique_frames = 0
        self.keep_mode = keep_mode
        self.disconnected = False
        self._n_lines = n_lines
        self._disconnect_after = disconnect_after
        self._has_captures = has_captures
        self._query_label = query_label
        self.saved = None
        self.new_seg = None
        self.journal = journal
        self.show_rulers = False
        self.editor = editor
        self.last_queries = []
        # View mode + retain-mode surface used by the view toggle / session info.
        self.view_mode = "full"
        self.keep_n = None
        self.transport_type = "slcan-tcp"
        # Segment metadata surfaced by the header bar.
        self.session_label = ""
        self.session_states = []
        self.session_notes = ""

    @property
    def interval(self) -> float:
        return self.schedule.interval

    @interval.setter
    def interval(self, v: float) -> None:
        self.schedule.interval = v

    def seconds_until_due(self) -> float:
        import time

        return self.schedule.seconds_until_due(time.monotonic())

    def segment_title(self) -> str:
        return self.session_label or self._query_label or "Monitor"

    def cycle_view(self) -> str:
        from canair.live._monitor_render import VIEW_MODES

        idx = VIEW_MODES.index(self.view_mode) if self.view_mode in VIEW_MODES else 0
        self.view_mode = VIEW_MODES[(idx + 1) % len(VIEW_MODES)]
        return self.view_mode

    def session_summary(self) -> dict:
        from datetime import datetime

        return {
            "recording": self.journal is not None,
            "label": self.segment_title(),
            "states": list(self.session_states),
            "notes": self.session_notes,
            "query": self._query_label,
            "keep_mode": self.keep_mode,
            "keep_n": self.keep_n,
            "view_mode": self.view_mode,
            "interval": self.interval,
            "schedule": self.schedule.describe(),
            "cycle": self.cycle,
            "total_frames": self.total_frames,
            "unique_frames": self.unique_frames,
            "transport": self.transport_type,
            "captures_dir": "/tmp/captures",
            "journal_path": "/tmp/captures/.journal/20260806T101010-42.jsonl"
            if self.journal is not None
            else None,
            "run_started_at": datetime.now(),
            "segment_started_at": datetime.now(),
            "segment_frames": self.total_frames,
            "completed_segments": 0,
        }

    def segment_history(self) -> list[dict]:
        return []

    async def poll_once(self):
        self.cycle += 1
        self.elapsed = 0.01
        self.last_cmds = 3
        self.last_elm_time = 0.02
        self.total_frames += 2
        self.unique_frames += 1
        if self._disconnect_after and self.cycle >= self._disconnect_after:
            self.disconnected = True

    def render(self) -> Text:
        t = Text()
        t.append(f"  Monitor cycle {self.cycle}\n", style="dim")
        for i in range(self._n_lines):
            t.append(f"  BMS 21{i:02X}: value={self.cycle * i}\n")
        return t

    def has_captures(self) -> bool:
        return self._has_captures

    def query_label(self) -> str:
        return self._query_label

    def state_options(self):
        return [
            ("charging", "HV battery actively charging."),
            ("ready", "HV active, driveable."),
            ("parked", "Stationary, gear in Park."),
            ("sleep", ""),
        ]

    def save_now(self, label, vehicle_states=None, notes=None) -> str:
        self.saved = (label, vehicle_states, notes)
        return "Saved 1 payload → foo.yaml"

    def new_segment(self, label, vehicle_states=None, notes=None) -> str:
        self.new_seg = (label, vehicle_states, notes)
        return "Session saved → foo.yaml. Now recording."


class FakeEditor:
    """Records calls from the TUI without touching disk or the CAN bus."""

    _ITEMS: ClassVar = [("BMS (0x7E4)", "2101", "SOC"), ("BMS (0x7E4)", "2101", "TEMP")]
    _FILTERS = ("all", "verified", "unverified", "enabled", "disabled")

    def __init__(self):
        self.selected = None
        self.filter_mode = "all"
        self.applied = None
        self.verified_toggles = 0
        self.enabled_toggles = 0

    def move(self, _last_queries, delta):
        if self.selected is None:
            self.selected = self._ITEMS[0] if delta >= 0 else self._ITEMS[-1]
        else:
            i = self._ITEMS.index(self.selected)
            self.selected = self._ITEMS[max(0, min(len(self._ITEMS) - 1, i + delta))]
        return self.selected

    def cycle_filter(self, _last_queries=None):
        self.filter_mode = self._FILTERS[
            (self._FILTERS.index(self.filter_mode) + 1) % len(self._FILTERS)
        ]
        return self.filter_mode

    def ensure_valid(self, _last_queries):
        pass

    def clear_selection(self):
        had = self.selected is not None
        self.selected = None
        return had

    def selection_label(self):
        if self.selected is None:
            return ""
        ecu, pid, name = self.selected
        return f"{ecu.split()[0]} {pid} {name}"

    def edit_target(self):
        if self.selected is None:
            return None
        return {
            "ecu": "BMS",
            "pid": "2101",
            "name": self.selected[2],
            "expression": "B4",
            "unit": "%",
            "min": "",
            "max": "",
            "notes": "",
            "verified": False,
            "enabled": True,
        }

    def apply_edit(self, fields):
        self.applied = fields
        return "Saved BMS 2101 SOC"

    def toggle_verified(self):
        self.verified_toggles += 1
        return "SOC verified=true"

    def toggle_enabled(self):
        self.enabled_toggles += 1
        return "SOC enabled=false"
