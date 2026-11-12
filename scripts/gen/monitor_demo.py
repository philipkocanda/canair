#!/usr/bin/env python3
"""Run the real monitor TUI on a recorded drive, for the docs' screenshots and recordings.

The monitor needs a bus, and the docs must be reproducible on any machine with no device, so
this script stands a *replay terminal* in for the dongle. It answers each request with the next
payload recorded for that ECU and PID in one capture session of the bundled Ioniq profile (a
motorway drive in READY), so the values move as they did on the road. Everything else is the
production code: the controller, the poll loop, the decoders, the renderer and the Textual app.

The app runs on a throwaway copy of the profile in a temporary directory: its recording is
journaled there, and an edit made in it (``v`` toggles a signal's verified flag) lands there,
never in the repo. The link zone names the transport ``replay``, so a screenshot never claims to
be live.

    uv run python scripts/gen/monitor_demo.py                    # the app, in this terminal
    uv run python scripts/gen/monitor_demo.py --svg out.svg --size 120x34 --keys down,down
    uv run python scripts/gen/monitor_demo.py --check            # a few headless cycles

``gen_screenshots.py`` runs it for the ``kind: tui`` shots, and the monitor tape runs it in vhs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from itertools import cycle
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))  # tests.support: the fake terminal the replay builds on

os.environ.setdefault("CANAIR_VARIANT", "28kwh")
os.environ.setdefault("CANAIR_NO_UPDATE_CHECK", "1")

from canair.captures.io import load_sessions, session_captures  # noqa: E402
from canair.ecus.pids import load_pids  # noqa: E402
from canair.live.monitor.controller import MonitorController  # noqa: E402
from canair.live.monitor.options import MonitorOptions  # noqa: E402
from canair.live.monitor.record_write import open_journal  # noqa: E402
from canair.profile.discovery import resolve_profile  # noqa: E402
from canair.query.steps import parse_sub_commands  # noqa: E402
from canair.tui.apps.monitor.app import MonitorApp, run_monitor_app  # noqa: E402
from canair.variants.selection import apply_variant  # noqa: E402
from tests.support.fakes import FakeTerminal, ok  # noqa: E402

PROFILE = "hyundai-ioniq-ae-ev"
#: The recorded drive: (capture file, session index). READY, about 100 km/h on a motorway.
SESSION = ("2026-08-08.json", 6)
QUERY = "query BMS:2101 VCU:2101 MCU:2102"
LABEL = "Motorway drive — battery and drivetrain"
STATES = "READY, DRIVING"


class ReplayTerminal(FakeTerminal):
    """Answers every request with the next payload recorded for it, round and round."""

    def __init__(self, recorded: dict[tuple[int, str], list[str]], tx_to_rx: dict[int, int]):
        super().__init__(session_ok=True)
        self._next = {key: cycle(payloads) for key, payloads in recorded.items()}
        self._rx = tx_to_rx
        self.diag.transport = "replay"
        # What the ELM327 path reports per cycle: commands sent and the seconds they took.
        self.cmd_count = 0
        self.cmd_time = 0.0

    async def send_uds(self, req, timeout=None, expected_sid=None, expected_echo=None, **kw):
        # A real request takes a few tens of milliseconds on the bus; so does this one, so the
        # poll loop paces itself as it does on the car.
        await asyncio.sleep(0.03)
        self.cmd_count += 1
        self.cmd_time += 0.03
        key = (self._rx.get(self.header or 0, 0), req.upper())
        if key in self._next:
            self.sent.append(req)
            return ok(next(self._next[key]))
        return await super().send_uds(req, timeout, expected_sid, expected_echo, **kw)


def _bundled_profile():
    return apply_variant(resolve_profile(PROFILE, REPO_ROOT / "profiles"))


def _scratch_profile(bundled, workdir: Path):
    """A copy of the profile's definitions in ``workdir``, so nothing the demo does reaches it."""
    copy = workdir / PROFILE
    shutil.copytree(
        bundled.root, copy, ignore=shutil.ignore_patterns("captures", "references", "out")
    )
    (copy / "captures").mkdir()
    return apply_variant(resolve_profile(str(copy)))


def _recorded(profile) -> dict[tuple[int, str], list[str]]:
    """``(rx id, request) → payloads`` of the recorded session, oldest first."""
    path = Path(profile.captures_dir) / SESSION[0]
    sessions = dict(load_sessions(path))
    recorded: dict[tuple[int, str], list[str]] = defaultdict(list)
    for cap in sorted(
        session_captures(sessions[SESSION[1]], path, SESSION[1]), key=lambda c: c.get("time", "")
    ):
        if cap.get("payload") and cap.get("rx"):
            recorded[(int(cap["rx"], 16), str(cap["pid"]).upper())].append(cap["payload"])
    return recorded


def _tx_to_rx(pids_data: dict) -> dict[int, int]:
    out = {}
    for ecu in pids_data.get("ecus", {}).values():
        tx = ecu.get("tx_id")
        if tx is not None:
            out[int(tx)] = int(ecu.get("rx_id") or int(tx) + 8)
    return out


async def build_controller(workdir: Path) -> MonitorController:
    """The production controller over the replay terminal, on a scratch profile in ``workdir``."""
    bundled = _bundled_profile()
    profile = _scratch_profile(bundled, workdir)
    pids_data = load_pids(profile)
    terminal = ReplayTerminal(_recorded(bundled), _tx_to_rx(pids_data))
    options = MonitorOptions(
        query_steps=parse_sub_commands([QUERY], profile=profile),
        interval=1.0,
        schedule="fixed",
        save=True,
        label=LABEL,
        vehicle_states=STATES,
        keep_mode="changes",
    )
    controller = MonitorController(terminal, options, pids_data, profile=profile)
    controller.transport_type = "replay"
    controller.captures_dir = Path(profile.captures_dir)
    controller.recorder.session_label = LABEL
    controller.recorder.session_states = [s.strip() for s in STATES.split(",")]
    controller.recorder.journal = open_journal(controller, LABEL, None, None)
    await controller.setup([])
    return controller


async def _svg(controller, out: Path, size: tuple[int, int], keys: list[str], wait: float):
    app = MonitorApp(controller)
    async with app.run_test(size=size) as pilot:
        await pilot.pause(wait)
        for key in keys:
            await pilot.press(key)
            await pilot.pause(0.3)
        await pilot.pause(0.5)
        out.write_text(app.export_screenshot(title="canair monitor"))


async def main_async(args) -> int:
    with tempfile.TemporaryDirectory(prefix="canair-demo-") as tmp:
        controller = await build_controller(Path(tmp))
        try:
            if args.check:
                for _ in range(3):
                    await controller.poll_once()
                return 0 if controller.cycle == 3 and controller.last_queries else 1
            if args.svg:
                width, height = (int(n) for n in args.size.split("x"))
                keys = [k for k in args.keys.split(",") if k]
                await _svg(controller, Path(args.svg), (width, height), keys, args.wait)
                return 0
            await run_monitor_app(controller)
            return 0
        finally:
            controller.recorder.journal.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--svg", help="write a screenshot here instead of running the app")
    parser.add_argument("--size", default="120x34", help="the screenshot's COLSxROWS")
    parser.add_argument("--keys", default="", help="comma-separated keys to press first")
    parser.add_argument("--wait", type=float, default=6.0, help="seconds to poll before the shot")
    parser.add_argument("--check", action="store_true", help="poll three cycles headless")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
