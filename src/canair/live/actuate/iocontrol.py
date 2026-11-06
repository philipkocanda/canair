"""IOControl mode — the interactive actuator view of ``canair io ECU``.

:class:`IOControlController` holds what the view shows and does: the DIDs of the current view
mode, what was sent to each and what came back, the diagnostic session, and the edits to
``ecus/<ecu>.yaml``. Its actuator (:mod:`.iocontrol_actuate`) puts the requests on the bus. The
screen itself is a Textual app (``tui/apps/iocontrol/``) that the CLI passes in as
``interactive``, because nothing below ``tui/`` imports Textual.

:func:`mode_iocontrol_tui` runs one session: it starts the opt-in ``--poll`` loop, runs the app,
then releases every actuator the session switched on and prints the final table.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from canair.core.paths import config_dir
from canair.core.terminal import is_interactive
from canair.ecus.pids import IoControlCommand, IoControlIndexEntry, build_iocontrol_index, load_pids
from canair.live.actuate.iocontrol_actuate import ACTUATOR_ON, ActuatorState, IOControlActuator
from canair.live.actuate.iocontrol_render import iocontrol_table, session_text
from canair.live.actuate.not_found import report_not_found
from canair.profile.model import Profile
from canair.render import glyphs, messages
from canair.render.console import console
from canair.transport.protocol import Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.protocol import DiagProtocol
from canair.yaml_edit.pids import PidsEditError, promote_discovery, update_iocontrol_field

_tui_logger = logging.getLogger("iocontrol-tui")

#: How a status message reads: the view colours it and gives it a glyph.
StatusLevel = Literal["error", "warning", "success", "info"]

ViewMode = Literal["curated", "all", "discoveries"]
VIEW_MODES: tuple[ViewMode, ...] = ("curated", "all", "discoveries")


def _log_file() -> Path:
    """The TUI debug log, cleared each session, next to canair's other logs."""
    return config_dir() / "logs" / "iocontrol-tui.log"


def _in_view(cmd: IoControlCommand, mode: ViewMode) -> bool:
    if mode == "curated":
        return not cmd.get("discovery")
    if mode == "discoveries":
        return bool(cmd.get("discovery"))
    return True


class IOControlController:
    """The state and the operations of the IOControl view, with no terminal I/O."""

    def __init__(
        self,
        terminal: Terminal,
        ecu_key: str,
        ecu_info: IoControlIndexEntry,
        pids_data: dict,
        verbose: bool = False,
        poll: bool = False,
        *,
        profile: Profile,
    ) -> None:
        self.profile = profile
        self.terminal = terminal
        self.ecu_key = ecu_key
        self.tx_id = ecu_info["tx_id"]
        # Full cmd map (curated + discoveries); the filtered view is in self.cmds.
        self.all_cmds: dict[str, IoControlCommand] = ecu_info["cmds"]
        self.pids_data = pids_data
        self.verbose = verbose
        # A KWP2000 ECU's actuators are service 0x30 (manufacturer-defined control option): an
        # ON needs a declared release, and UDS-shaped value keys are refused (safety.actuation).
        self.protocol: DiagProtocol = ecu_info["protocol"]
        # Background status polling is OPT-IN: the poll loop sends each DID's release
        # (2F{DID}00, returnControlToECU, on UDS), which can actuate relay/solenoid-backed DIDs
        # (audible click). Off by default so launching the view sends no CAN traffic.
        self.poll_enabled: bool = poll

        self.view_mode: ViewMode = "curated"
        self.cmds: dict[str, IoControlCommand] = {}
        self.dids: list[str] = []
        self._apply_view_filter()

        # Per-DID state: None=idle, else one of ActuatorState ("on"/"off"/"error").
        # Typed so a drifted sentinel can't silently disable release_all's
        # switch-everything-back-off safety net (see iocontrol_actuate).
        self.state: dict[str, ActuatorState | None] = dict.fromkeys(self.all_cmds)
        self.last_response: dict[str, str] = {}  # DID → last response text
        self.last_value: dict[str, bytes] = {}  # DID → last ShortTermAdjustment value bytes
        # Per-DID status: the controlStatusRecord tail of the last 0x2F response, as hex.
        # None = no response seen yet.
        self.status_bytes: dict[str, str | None] = dict.fromkeys(self.all_cmds)

        self.polling: bool = False  # True while the background poll loop runs
        self.session_active: bool = False
        # The mode of the open session: an actuator that needs another one (a Nissan
        # 10 C0 next to a UDS 10 03) re-enters instead of reusing it.
        self.session_mode: str | None = None
        self.sm = SessionManager(terminal, verbose=verbose)  # opens and keeps it alive
        self.stopping: bool = False
        self.busy: bool = False  # one CAN request at a time
        # The latest message for the user; the view shows it and clears it.
        self.status: str = ""
        self.status_level: StatusLevel = "info"

        self.actuator: IOControlActuator = IOControlActuator(self)

    # -- view mode ---------------------------------------------------------
    def _apply_view_filter(self) -> None:
        """Populate ``cmds``/``dids`` from ``all_cmds`` (YAML order) for the view mode."""
        self.cmds = {d: c for d, c in self.all_cmds.items() if _in_view(c, self.view_mode)}
        self.dids = list(self.cmds)

    def cycle_view(self) -> ViewMode:
        """Move to the next view mode that has DIDs, and return it."""
        idx = VIEW_MODES.index(self.view_mode)
        for step in range(1, len(VIEW_MODES) + 1):
            candidate = VIEW_MODES[(idx + step) % len(VIEW_MODES)]
            if any(_in_view(c, candidate) for c in self.all_cmds.values()):
                self.view_mode = candidate
                break
        self._apply_view_filter()
        return self.view_mode

    def counts(self) -> tuple[int, int]:
        """The number of curated entries and of unpromoted discoveries."""
        disc = sum(1 for c in self.all_cmds.values() if c.get("discovery"))
        return len(self.all_cmds) - disc, disc

    def active_dids(self) -> list[str]:
        """The DIDs this session switched on and has not released."""
        return [d for d, s in self.state.items() if s == ACTUATOR_ON]

    # -- messages ----------------------------------------------------------
    def report(self, text: str, level: StatusLevel = "info") -> None:
        self.status = text
        self.status_level = level

    def take_status(self) -> tuple[str, StatusLevel] | None:
        """The pending message, once: the view shows it and it is gone."""
        if not self.status:
            return None
        out = (self.status, self.status_level)
        self.report("")
        return out

    # -- edits to ecus/ ----------------------------------------------------
    def _reload(self) -> None:
        """Re-read the profile and rebuild the index, keeping the per-DID state."""
        fresh = load_pids(profile=self.profile)
        self.pids_data.clear()
        self.pids_data.update(fresh)
        idx = build_iocontrol_index(self.pids_data, include_discoveries=True)
        self.all_cmds = idx[self.ecu_key]["cmds"]
        self._apply_view_filter()
        for d in self.all_cmds:
            self.state.setdefault(d, None)
            self.status_bytes.setdefault(d, None)

    def apply_edit(self, did: str, field: str, value: str | bool) -> None:
        """Write one field of ``did`` to ecus/<ecu>.yaml and reload; report the result."""
        try:
            fpath = update_iocontrol_field(self.ecu_key, did, field, value, profile=self.profile)
        except PidsEditError as exc:
            self.report(f"Edit failed: {exc}", "error")
            _tui_logger.error("edit %s %s=%r: %s", did, field, value, exc)
            return
        try:
            self._reload()
        except Exception as exc:  # reload failure is non-fatal; in-memory stale
            _tui_logger.warning("reload after edit failed: %s", exc)
        self.report(f"Saved {field} on {did} {glyphs.ARROW} {fpath.name}", "success")
        _tui_logger.info("edit %s %s=%r -> %s", did, field, value, fpath)

    def apply_promote(self, did: str, label: str) -> None:
        """Promote a discovery to a curated entry (on/off inferred) and reload."""
        label = label.strip()
        if not label:
            self.report("Promote cancelled: empty label", "warning")
            return
        try:
            fpath = promote_discovery(self.ecu_key, did, label, profile=self.profile)
        except PidsEditError as exc:
            self.report(f"Promote failed: {exc}", "error")
            _tui_logger.error("promote %s label=%r: %s", did, label, exc)
            return
        try:
            self._reload()
        except Exception as exc:
            _tui_logger.warning("reload after promote failed: %s", exc)
        self.report(
            f"Promoted {did} {glyphs.ARROW} curated (on/off inferred) "
            f"{glyphs.EMPTY} check it and refine the label",
            "success",
        )
        _tui_logger.info("promote %s label=%r -> %s", did, label, fpath)


#: Runs the interactive view on a controller and returns when the user quits.
InteractiveView = Callable[[IOControlController], Awaitable[None]]


def _start_log() -> Path:
    """Attach the per-session debug log (cleared each session)."""
    log_file = _log_file()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, mode="w")
    handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
    _tui_logger.addHandler(handler)
    _tui_logger.setLevel(logging.DEBUG)
    return log_file


async def run_iocontrol_view(controller: IOControlController, interactive: InteractiveView) -> None:
    """Run the view, then release every actuator it switched on and print the final table."""
    c = controller
    if not c.dids:
        messages.fail(f"no IOControl DIDs available for {c.ecu_key}")
    if not is_interactive():
        messages.fail(
            "the IOControl view needs a terminal; use `canair io ECU --json` to list DIDs "
            "or `canair io ECU --did DID` to send one",
            code=2,
        )
    log_file = _start_log()
    _tui_logger.info("TUI session start — %s (0x%03X), %d DIDs", c.ecu_key, c.tx_id, len(c.dids))
    # The poll sends each DID's release (2F {DID} 00 on UDS), which is NOT a guaranteed silent
    # read: returnControlToECU hands the I/O back to the ECU's own logic and can physically
    # actuate relay/solenoid-backed DIDs (e.g. IGPM door lock/unlock, trunk, charge cable lock,
    # defogger), with an audible click on the first poll.
    poll_task = asyncio.ensure_future(c.actuator.status_poll_loop()) if c.poll_enabled else None
    try:
        await interactive(c)
    finally:
        c.stopping = True
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
        if c.active_dids():
            messages.note(f"releasing {', '.join(c.active_dids())}{glyphs.ELLIPSIS}")
        await c.actuator.cleanup()
        out = console()
        out.print(session_text(c))
        out.print(iocontrol_table(c))
        _tui_logger.info("TUI session end")
        messages.note(f"debug log: {log_file}")


async def mode_iocontrol_tui(
    terminal: Terminal,
    pids_data: dict,
    ecu_name: str,
    verbose: bool = False,
    poll: bool = False,
    *,
    profile: Profile,
    interactive: InteractiveView,
) -> None:
    """Interactive IOControl view for an ECU."""
    ioctrl_index = build_iocontrol_index(pids_data, include_discoveries=True)
    ecu_key = ecu_name.upper()

    if ecu_key not in ioctrl_index:
        report_not_found(
            f"no IOControl DIDs for ECU: {ecu_name}", ioctrl_index.keys(), "ECUs with IOControl"
        )
        raise SystemExit(1)

    ecu_info = ioctrl_index[ecu_key]
    if poll and not any(c["release"] for c in ecu_info["cmds"].values()):
        # The poll sends each entry's release. Service 0x30's control-option byte
        # is manufacturer-defined, so a KWP2000 entry has one only when declared.
        messages.fail(
            f"--poll sends each actuator's release, and no {ecu_key} entry has one "
            "(a KWP2000 0x30 entry needs a declared release:); run without --poll",
            code=2,
        )

    controller = IOControlController(
        terminal, ecu_key, ecu_info, pids_data, verbose, poll, profile=profile
    )
    await run_iocontrol_view(controller, interactive)
