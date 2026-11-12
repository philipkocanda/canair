"""Tests for scripts/gen/monitor_demo.py: the monitor on a recorded drive, for the docs.

The demo must run the production monitor with no device, show values that move, and never
write to the bundled profile: a recording or an edit made in it lands in a scratch copy.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "gen" / "monitor_demo.py"


@pytest.fixture(scope="module")
def demo():
    spec = importlib.util.spec_from_file_location("monitor_demo", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_replay_moves_the_values_and_stays_out_of_the_repo(demo, tmp_path):
    async def run():
        controller = await demo.build_controller(tmp_path)
        payloads = []
        for _ in range(4):
            await controller.poll_once()
            entry = controller.last_queries[0][1][0]
            payloads.append(entry["raw_hex"])
        return controller, payloads

    controller, payloads = asyncio.run(run())
    assert len(set(payloads)) > 1  # the recorded drive plays on, cycle by cycle
    assert controller.transport_type == "replay"
    # The definitions, the captures and the journal are all in the scratch copy.
    assert Path(controller.profile.root).is_relative_to(tmp_path)
    assert Path(controller.captures_dir).is_relative_to(tmp_path)


def test_the_actuator_pane_is_offered_without_switching_anything(demo, tmp_path):
    """--actuate builds the pane's rows from the profile; the replay sends nothing to switch."""

    async def run():
        controller = await demo.build_controller(tmp_path, actuate=("IGPM",))
        await controller.poll_once()
        return controller

    controller = asyncio.run(run())
    assert controller.actuation is not None and controller.actuation.actuators()
    assert controller.actuation.on_count() == 0
    assert not any(req.startswith("2F") for req in controller.terminal.sent)
