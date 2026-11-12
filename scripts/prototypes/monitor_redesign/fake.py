"""A fake monitor session for the redesign mockups: real profile data, no device.

The values pane is drawn by the production renderer (``live/monitor/render.py``) from payloads
recorded on the bundled Ioniq, so the mockups judge the chrome around the real content. Each
tick nudges a few bytes that feed live-looking signals (current, power, temperatures), and the
counters, link health and recording state move the way a session does.
"""

from __future__ import annotations

import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from rich.text import Text

from canair.core.paths import BUNDLED_PROFILES_DIR
from canair.ecus.pids import load_pids_dir
from canair.expression.decoding import decode_param_rows
from canair.live.monitor.render import _render_results
from canair.render.formatting import changed_param_highlights, format_value
from canair.render.glyphs import OK

PROFILE = "hyundai-ioniq-ae-ev"

#: Below this pane width the values table is the compact one (see ``render_compact``).
COMPACT = 96

#: Payloads recorded on the car (``canair captures uds ECU PID --latest``).
PAYLOADS = {
    ("BMS", "2101"): "6101FFFFFFFFAB1B522648A3FFEB0F1F080707080707080007C903C90100009300059392000"
    "590A6000205790001F4BD016328510901820000000003E8",
    ("BMS", "2105"): "6105FFFFFFFF00000000000707070707070726482648000164060603E80203E8018E002900"
    "0000000000000000",
    ("VCU", "2101"): "6101FFE000000921104F06450300000000F678000720",
    ("MCU", "2101"): "6101000003FF083801E3938E3A0000000A0035025204543A00001800E8FF",
    ("OBC", "2101"): "6101FFFFF8000099EA96495777010B99024B00BB05BC43102000C802100062760012000B01"
    "F3963E73880013",
}

#: Signals whose bytes the fake session nudges, so values and byte highlights move.
LIVE_WORDS = ("CURRENT", "POWER", "TEMP", "VOLTAGE", "RPM", "FEEDBACK")

VIEW_MODES = ("ecus", "ranges", "signals", "full")
FILTERS = ("all", "verified", "unverified", "ship", "no-ship")


@dataclass
class Health:
    rtt_ms: float = 12.0
    drops: int = 0
    stale: int = 0
    resyncs: int = 0
    errors: int = 0

    @property
    def verdict(self) -> str:
        """``ok``, ``coping`` (resyncs, errors) or ``lossy`` (drops): what the link is doing."""
        if self.drops or self.stale:
            return "lossy"
        if self.resyncs or self.errors:
            return "coping"
        return "ok"


@dataclass
class FakeMonitor:
    """Everything a monitor screen shows, with the state changes the mockups' commands make."""

    device: str = "home"
    host: str = "192.168.1.40"
    transport: str = "wican-ws"
    label: str = "Overnight charge — battery"
    states: list[str] = field(default_factory=lambda: ["CHARGING", "PLUGGED"])
    notes: str = "Type 2 at 11 kW, ambient 6 °C, fan audible from 23:10"
    recording: bool = True
    paused: bool = False
    reconnecting: bool = False
    schedule: str = "fixed"
    interval: float = 2.0
    view_mode: str = "full"
    filter_mode: str = "all"
    show_rulers: bool = False
    actuate: bool = True
    actuators_on: int = 0
    sessions: dict[str, str] = field(default_factory=lambda: {"BMS": "81", "VCU": "03"})
    suggested_state: str = "CHARGING"
    selected: tuple[str, str, str] | None = None
    polled: list[tuple[str, str]] = field(
        default_factory=lambda: [
            ("BMS", "2101"),
            ("BMS", "2105"),
            ("VCU", "2101"),
            ("MCU", "2101"),
            ("OBC", "2101"),
        ]
    )

    def __post_init__(self) -> None:
        self.defs = load_pids_dir(Path(BUNDLED_PROFILES_DIR) / PROFILE / "ecus")["ecus"]
        self.payloads = dict(PAYLOADS)
        self.prev: dict[tuple[str, str], str] = {}
        self.prev_values: dict[tuple[str, str], dict[str, object]] = {}
        self.history: dict[tuple[str, str, str], deque[float]] = {}
        self.cycle = 0
        self.captured = 0
        self.unique = 0
        self.started = time.monotonic()
        self.segment_started = time.monotonic() - 252
        self.health = Health()
        self.live_bytes = {key: self._live_bytes(key) for key in self.payloads}
        self.segments = 2
        # A session some minutes old, so the counters and the history look lived in.
        for _ in range(40):
            self.tick()

    # -- the profile ---------------------------------------------------------------
    def label_of(self, ecu: str) -> str:
        return f"{ecu} (0x{int(self.defs[ecu]['tx_id']):03X})"

    def pid_def(self, ecu: str, pid: str) -> dict:
        return {str(k): v for k, v in self.defs[ecu]["pids"].items()}[pid]

    def tree(self) -> dict[str, list[tuple[str, int]]]:
        """``{ECU: [(PID, signal count), …]}`` for the ECUs this session can poll."""
        out: dict[str, list[tuple[str, int]]] = {}
        for ecu in ("BMS", "VCU", "MCU", "OBC", "CLU", "HVAC"):
            pids = self.defs[ecu].get("pids") or {}
            out[ecu] = [(str(p), len(d.get("parameters") or {})) for p, d in pids.items()][:8]
        return out

    def rows(self, ecu: str, pid: str) -> list:
        return decode_param_rows(self.payloads[(ecu, pid)], self.pid_def(ecu, pid)["parameters"])

    def _live_bytes(self, key: tuple[str, str]) -> list[int]:
        """Payload byte positions that move a live-looking signal when nudged."""
        base = self.rows(*key)
        payload = bytearray.fromhex(self.payloads[key])
        live: list[int] = []
        for i in range(4, len(payload)):
            bumped = bytearray(payload)
            bumped[i] = (bumped[i] + 1) % 256
            rows = decode_param_rows(bumped.hex().upper(), self.pid_def(*key)["parameters"])
            moved = {a[0]: (a[1], b[1]) for a, b in zip(base, rows, strict=False) if a[1] != b[1]}
            small = all(
                isinstance(x, int | float)
                and isinstance(y, int | float)
                and abs(x - y) <= 0.05 * max(1.0, abs(x))
                for x, y in moved.values()
            )
            if moved and small and all(any(w in n for w in LIVE_WORDS) for n in moved):
                live.append(i)
        return live[:6]

    # -- time passing ---------------------------------------------------------------
    def tick(self) -> None:
        """One poll cycle: nudge the live bytes, count frames, now and then upset the link."""
        if self.paused or self.reconnecting:
            return
        self.cycle += 1
        for key in self.polled:
            self.prev[key] = self.payloads[key]
            self.prev_values[key] = {r[0]: r[1] for r in self.rows(*key)}
            payload = bytearray.fromhex(self.payloads[key])
            origin = bytes.fromhex(PAYLOADS[key])
            for i in random.sample(self.live_bytes[key], k=min(2, len(self.live_bytes[key]))):
                step = random.choice((-1, 1))
                if abs(payload[i] + step - origin[i]) > 3:
                    step = -step
                payload[i] = max(0, min(255, payload[i] + step))
            self.payloads[key] = payload.hex().upper()
            for row in self.rows(*key):
                if isinstance(row[1], int | float):
                    hist = self.history.setdefault((*key, row[0]), deque(maxlen=60))
                    hist.append(float(row[1]))
        self.captured += len(self.polled)
        self.unique += sum(1 for k in self.polled if self.prev[k] != self.payloads[k])
        self.health.rtt_ms = max(4.0, self.health.rtt_ms + random.uniform(-3, 3))
        if self.cycle % 45 == 0:
            self.health.resyncs += 1
        if self.cycle % 97 == 0:
            self.health.drops += 1

    # -- what the screen shows ------------------------------------------------------
    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.segment_started

    @property
    def rate(self) -> str:
        return f"{self.interval:.1f}s" if self.schedule == "fixed" else "per-PID"

    def queries(self) -> list:
        frames: dict[str, list] = {}
        for ecu, pid in self.polled:
            rows = self.rows(ecu, pid)
            if self.filter_mode == "verified":
                rows = [r for r in rows if r[5]]
            elif self.filter_mode == "unverified":
                rows = [r for r in rows if not r[5]]
            entry = {"pid": pid, "params": rows, "raw_hex": self.payloads[(ecu, pid)]}
            frames.setdefault(self.label_of(ecu), []).append(entry)
        return list(frames.items())

    def render(self, width: int = 200) -> Text:
        if width < COMPACT:
            return self.render_compact(width)
        prev = {(self.label_of(e), p): v for (e, p), v in self.prev.items()}
        prev_vals = {(self.label_of(e), p): v for (e, p), v in self.prev_values.items()}
        body = _render_results(
            self.queries(),
            False,
            self.cycle,
            0.4,
            self.rate,
            prev,
            prev_params=prev_vals,
            show_rulers=self.show_rulers,
            footer=False,
            selected=self.selected,
            view_mode=self.view_mode,
        )
        # The renderer's "Monitor — cycle N" line repeats what every redesigned status bar
        # carries, so the mockups drop it.
        return body[body.plain.index("\n") + 1 :]

    def render_compact(self, width: int) -> Text:
        """The values table for a narrow pane: one line per signal, nothing wraps.

        The production table pads every column to its widest cell and wraps on a phone. Here
        the name gives way (``…``) so the value and the verified mark stay on the line.
        """
        prev = {(self.label_of(e), p): v for (e, p), v in self.prev.items()}
        prev_vals = {(self.label_of(e), p): v for (e, p), v in self.prev_values.items()}
        t = Text()
        for label, entries in self.queries():
            t.append(f"\n{label}\n", style="bold cyan")
            for e in entries:
                key = (label, e["pid"])
                t.append(f"  {e['pid']} ", style="cyan")
                t.append("○", style="green")
                if prev.get(key) not in (None, e["raw_hex"]):
                    t.append(" ●", style="bright_green")
                t.append("\n")
                if self.view_mode == "ecus":
                    continue
                highlights = changed_param_highlights(
                    e["params"], e["raw_hex"], prev.get(key, ""), prev_vals.get(key)
                )
                for row in e["params"]:
                    name, value, unit, verified = row[0], row[1], row[2], row[5]
                    val = format_value(value, unit, row[6] if len(row) > 6 else "")
                    selected = self.selected == (label, e["pid"], name)
                    room = max(6, width - len(val) - 7)
                    shown = name if len(name) <= room else name[: room - 1] + "…"
                    style = "reverse bold" if selected else highlights.get(name, "")
                    t.append("  ▶ " if selected else "    ")
                    t.append(shown.ljust(room), style=style)
                    t.append(f" {val}", style=style)
                    t.append(
                        f" {OK}" if verified else " ?", style="green" if verified else "yellow"
                    )
                    t.append("\n")
        return t

    def signals(self) -> list[tuple[str, str, str]]:
        return [
            (label, e["pid"], r[0])
            for label, entries in self.queries()
            for e in entries
            for r in e["params"]
        ]

    def move(self, delta: int) -> None:
        sigs = self.signals()
        if not sigs:
            return
        i = sigs.index(self.selected) + delta if self.selected in sigs else 0
        self.selected = sigs[max(0, min(len(sigs) - 1, i))]

    def selected_detail(self) -> dict | None:
        """The inspector's view of the selected signal."""
        if self.selected is None:
            return None
        label, pid, name = self.selected
        ecu = label.split()[0]
        pdef = self.pid_def(ecu, pid)["parameters"][name]
        row = next(r for r in self.rows(ecu, pid) if r[0] == name)
        hist = list(self.history.get((ecu, pid, name), ()))
        refs = sorted(set(re.findall(r"B\d+", pdef.get("expression", ""))))
        return {
            "ecu": ecu,
            "pid": pid,
            "name": name,
            "value": row[1],
            "unit": row[2] or "",
            "expression": pdef.get("expression", ""),
            "bytes": refs,
            "verified": bool(row[5]),
            "ship": pdef.get("ship", True) is not False,
            "notes": " ".join(str(pdef.get("notes") or "").split()),
            "min": min(hist) if hist else None,
            "max": max(hist) if hist else None,
            "history": hist,
        }

    # -- the commands ---------------------------------------------------------------
    def cycle_view(self) -> str:
        self.view_mode = VIEW_MODES[(VIEW_MODES.index(self.view_mode) + 1) % len(VIEW_MODES)]
        return self.view_mode

    def cycle_filter(self) -> str:
        self.filter_mode = FILTERS[(FILTERS.index(self.filter_mode) + 1) % len(FILTERS)]
        if self.selected not in self.signals():
            self.selected = None
        return self.filter_mode

    def faster(self) -> str:
        self.interval = max(0.5, self.interval / 2)
        return self.rate

    def slower(self) -> str:
        self.interval = min(30.0, self.interval * 2)
        return self.rate
