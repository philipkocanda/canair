"""Fake controller and editor for the monitor TUI tests."""

from typing import ClassVar

from rich.text import Text


class FakeRecorder:
    """Stand-in for MonitorRecorder: the values the TUI reads and the saves it triggers."""

    def __init__(self, *, journal=None, has_captures=True):
        self.journal = journal
        self._has_captures = has_captures
        self.total_frames = 0
        self.unique_frames = 0
        self.segments: list[dict] = []
        self.saved = None
        self.new_seg = None
        # Segment metadata surfaced by the header bar.
        self.session_label = ""
        self.session_states = []
        self.session_notes = ""

    def has_captures(self) -> bool:
        return self._has_captures

    def offer_states(self, *, next_segment: bool = False) -> list[str]:
        return []

    def save_now(self, label, vehicle_states=None, notes=None) -> str:
        self.saved = (label, vehicle_states, notes)
        return "Saved 1 payload → foo.yaml"

    def new_segment(self, label, vehicle_states=None, notes=None) -> str:
        self.new_seg = (label, vehicle_states, notes)
        return "Session saved → foo.yaml. Now recording."


class FakeState:
    """Stand-in for MonitorStateSuggester."""

    def suggested_state(self):
        return None

    def state_options(self):
        return [
            ("charging", "HV battery actively charging."),
            ("ready", "HV active, driveable."),
            ("parked", "Stationary, gear in Park."),
            ("sleep", ""),
        ]


class FakeSessions:
    """Stand-in for MonitorSessions: no ECU has an open session type."""

    def active_modes(self) -> dict[str, str]:
        return {}

    async def keep_alive(self) -> None:
        self.kept_alive = getattr(self, "kept_alive", 0) + 1


class FakeView:
    """Stand-in for MonitorView."""

    def __init__(self, owner):
        self.o = owner

    def query_label(self) -> str:
        return self.o._query_label

    def segment_title(self) -> str:
        return self.o.recorder.session_label or self.o._query_label or "Monitor"

    def cycle(self) -> str:
        from canair.live.monitor.view import VIEW_MODES

        o = self.o
        idx = VIEW_MODES.index(o.view_mode) if o.view_mode in VIEW_MODES else 0
        o.view_mode = VIEW_MODES[(idx + 1) % len(VIEW_MODES)]
        return o.view_mode

    def session_summary(self) -> dict:
        from datetime import datetime

        o = self.o
        return {
            "recording": o.recorder.journal is not None,
            "label": self.segment_title(),
            "states": list(o.recorder.session_states),
            "notes": o.recorder.session_notes,
            "query": o._query_label,
            "keep_mode": o.keep_mode,
            "keep_n": o.keep_n,
            "view_mode": o.view_mode,
            "interval": o.schedule.interval,
            "schedule": o.schedule.describe(),
            "cycle": o.cycle,
            "total_frames": o.recorder.total_frames,
            "unique_frames": o.recorder.unique_frames,
            "transport": o.transport_type,
            "captures_dir": "/tmp/captures",
            "journal_path": "/tmp/captures/.journal/20260806T101010-42.jsonl"
            if o.recorder.journal is not None
            else None,
            "run_started_at": datetime.now(),
            "segment_started_at": datetime.now(),
            "segment_frames": o.recorder.total_frames,
            "completed_segments": 0,
        }


class FakeRenderer:
    """Stand-in for MonitorRenderer: a canned body."""

    def __init__(self, owner):
        self.o = owner

    def render(self) -> Text:
        o = self.o
        t = Text()
        t.append(f"  Monitor cycle {o.cycle}\n", style="dim")
        for i in range(o._n_lines):
            t.append(f"  BMS 21{i:02X}: value={o.cycle * i}\n")
        return t


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
        from canair.live.monitor.schedule import PollScheduler

        self.schedule = PollScheduler("fixed", 0.05)
        self.last_cmds = 0
        self.last_elm_time = 0.0
        self.recorder = FakeRecorder(journal=journal, has_captures=has_captures)
        self.state = FakeState()
        self.sessions = FakeSessions()
        self.view = FakeView(self)
        self.renderer = FakeRenderer(self)
        self.keep_mode = keep_mode
        self.disconnected = False
        self._n_lines = n_lines
        self._disconnect_after = disconnect_after
        self._query_label = query_label
        self.show_rulers = False
        self.editor = editor
        self.last_queries = []
        # View mode + retain-mode surface used by the view toggle / session info.
        self.view_mode = "full"
        self.keep_n = None
        self.transport_type = "slcan-tcp"

    def seconds_until_due(self) -> float:
        import time

        return self.schedule.seconds_until_due(time.monotonic())

    async def poll_once(self):
        self.cycle += 1
        self.elapsed = 0.01
        self.last_cmds = 3
        self.last_elm_time = 0.02
        self.recorder.total_frames += 2
        self.recorder.unique_frames += 1
        if self._disconnect_after and self.cycle >= self._disconnect_after:
            self.disconnected = True


class FakeEditor:
    """Records calls from the TUI without touching disk or the CAN bus."""

    _ITEMS: ClassVar = [("BMS (0x7E4)", "2101", "SOC"), ("BMS (0x7E4)", "2101", "TEMP")]
    _FILTERS = ("all", "verified", "unverified", "ship", "no-ship")

    def __init__(self):
        self.selected = None
        self.filter_mode = "all"
        self.applied = None
        self.verified_toggles = 0
        self.ship_toggles = 0

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
            "ship": True,
        }

    def apply_edit(self, fields):
        self.applied = fields
        return "Saved BMS 2101 SOC"

    def toggle_verified(self):
        self.verified_toggles += 1
        return "SOC verified=true"

    def toggle_ship(self):
        self.ship_toggles += 1
        return "SOC ship=false"
