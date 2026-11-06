"""IOControl mode — list, execute, and interactive TUI for IOControl commands.

The TUI mode (--iocontrol ECU with a WiCAN connection) uses alternate screen
buffer + raw ANSI rendering for keyboard-driven actuator toggling.
"""

import asyncio
import logging
import os
import sys
import termios
import tty
from pathlib import Path

from canair.core.paths import config_dir
from canair.core.terminal import is_interactive
from canair.core.terminal import terminal_lines as _terminal_lines
from canair.ecus.pids import IoControlCommand, IoControlIndexEntry, build_iocontrol_index, load_pids
from canair.live.actuate.iocontrol_actuate import ActuatorState, IOControlActuator
from canair.live.actuate.iocontrol_render import render_iocontrol
from canair.live.actuate.not_found import report_not_found
from canair.profile.model import Profile
from canair.render import ansi, glyphs, messages
from canair.transport.protocol import Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.protocol import DiagProtocol
from canair.yaml_edit.pids import PidsEditError, promote_discovery, update_iocontrol_field

_tui_logger = logging.getLogger("iocontrol-tui")


def _log_file() -> Path:
    """The TUI debug log, cleared each session, next to canair's other logs."""
    return config_dir() / "logs" / "iocontrol-tui.log"


# ── TUI ──────────────────────────────────────────────────────────────────────


class _IOControlTUI:
    """Interactive IOControl TUI — navigate DIDs and toggle ON/OFF.

    Keys:
        ↑/↓ or j/k  Navigate
        Enter/Space  Toggle ON (or OFF if already ON). If the DID has no
                     simple ON command, opens the value prompt instead
                     (seeded with the last value sent this session, or 00).
        o            Send explicit OFF (the entry's off command)
        v            Enter hex value → send ShortTermAdjustment (2F{DID}03{hex})
        +/-          Increment/decrement last value sent to current DID
        e            Edit label (writes to ecus/<ecu>.yaml)
        n            Edit notes (writes to ecus/<ecu>.yaml)
        m            Toggle verified (writes to ecus/<ecu>.yaml)
        d            Cycle view: curated / all / discoveries
        P            Promote current discovery to curated entry (on/off
                     inferred from response length; prompts for label)
        q / Ctrl+C   Quit (release every actuator switched on)
    """

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
        # Full cmd map (curated + discoveries); filtered view is in self.cmds.
        self.all_cmds: dict[str, IoControlCommand] = ecu_info["cmds"]
        self.pids_data = pids_data
        self.verbose = verbose
        # A KWP2000 ECU's actuators are service 0x30 (manufacturer-defined control option): an
        # ON needs a declared release, and UDS-shaped value keys are refused (safety.actuation).
        self.protocol: DiagProtocol = ecu_info["protocol"]
        # Background status polling is OPT-IN: the poll loop sends each DID's release
        # (2F{DID}00, returnControlToECU, on UDS), which can actuate relay/solenoid-backed DIDs
        # (audible click). Off by default so launching the TUI sends no CAN traffic.
        self._poll_enabled: bool = poll

        # View mode cycles through:
        #   "curated"     — only hand-authored iocontrol: entries
        #   "all"         — curated + scanner-discovered entries
        #   "discoveries" — only unpromoted discoveries (for triage)
        # Starts on "curated" so existing muscle memory is unchanged; user
        # presses 'd' to cycle to review newly scanned DIDs.
        self.view_mode: str = "curated"
        self._apply_view_filter()

        self.cursor: int = 0

        # Per-DID state: None=idle, else one of ActuatorState ("on"/"off"/"error").
        # Typed so a drifted sentinel can't silently disable release_all's
        # switch-everything-back-off safety net (see iocontrol_actuate).
        self.state: dict[str, ActuatorState | None] = dict.fromkeys(self.dids)
        self.last_response: dict[str, str] = {}  # DID → last response text
        self.last_value: dict[str, bytes] = {}  # DID → last ShortTermAdjustment value bytes

        # Per-DID status: trailing bytes (controlStatusRecord) from the last
        # 0x2F response for this DID, rendered as hex. Populated by both the
        # background poll loop (2F {DID} 00 returnControlToECU) and by every
        # ON/OFF/ADJUST send. None = never seen a response yet.
        self.status_bytes: dict[str, str | None] = dict.fromkeys(self.dids)
        self._status_polling: bool = False  # True while background poll loop is running
        self._session_active: bool = False
        # The mode of the open session: an actuator that needs another one (a Nissan
        # 10 C0 next to a UDS 10 03) re-enters instead of reusing it.
        self._session_mode: str | None = None
        self.sm = SessionManager(terminal, verbose=verbose)  # opens and keeps it alive
        self._quit: bool = False
        self._busy: bool = False  # prevent concurrent CAN commands
        self._status: str = ""  # bottom status line
        self._hex_input: str | None = None  # None = not in input mode, "" = editing
        # Metadata-edit input state. Tuple of (field, buffer) where field is
        # one of "label" / "notes"; None = not in edit mode. verified is
        # toggled directly (no input buffer).
        self._edit_input: tuple[str, str] | None = None

        # Scroll viewport: index of the first DID rendered on screen. Auto-
        # adjusted in ``_clamp_viewport()`` so the cursor is always visible.
        self._scroll_top: int = 0

        # CAN-facing session/actuation behaviour lives in the actuator
        # collaborator; it reads/updates this TUI's state via a back-reference,
        # keeping the class focused on view state, input, rendering, and edits.
        self.actuator: IOControlActuator = IOControlActuator(self)

    def _apply_view_filter(self) -> None:
        """Populate ``self.cmds`` / ``self.dids`` from ``self.all_cmds`` per view mode.

        Preserves insertion order from ``all_cmds`` (which in turn reflects
        YAML order: curated first, then discoveries). Called by ``__init__``
        and every time ``self.view_mode`` changes.
        """
        if self.view_mode == "curated":
            filtered = {d: c for d, c in self.all_cmds.items() if not c.get("discovery")}
        elif self.view_mode == "discoveries":
            filtered = {d: c for d, c in self.all_cmds.items() if c.get("discovery")}
        else:  # "all"
            filtered = dict(self.all_cmds)
        self.cmds: dict[str, IoControlCommand] = filtered
        self.dids: list[str] = list(filtered.keys())

    def _cycle_view(self) -> None:
        """Advance ``view_mode`` and re-apply the filter. Resets cursor/scroll."""
        order = ("curated", "all", "discoveries")
        try:
            idx = order.index(self.view_mode)
        except ValueError:
            idx = 0
        # Skip modes that would produce an empty list (e.g. no discoveries
        # in this ECU at all) so the user doesn't hit a blank screen.
        for step in range(1, len(order) + 1):
            candidate = order[(idx + step) % len(order)]
            probe_cmds = self.all_cmds
            if candidate == "curated":
                probe = [d for d, c in probe_cmds.items() if not c.get("discovery")]
            elif candidate == "discoveries":
                probe = [d for d, c in probe_cmds.items() if c.get("discovery")]
            else:
                probe = list(probe_cmds.keys())
            if probe:
                self.view_mode = candidate
                break
        self._apply_view_filter()
        # Reset per-DID state dicts for any new DIDs (missing keys would KeyError).
        for d in self.dids:
            self.state.setdefault(d, None)
            self.status_bytes.setdefault(d, None)
        self.cursor = 0
        self._scroll_top = 0

    def _render(self) -> str:
        """Build the display as a plain string with ANSI codes.

        Delegated to :func:`canair.live.actuate.iocontrol_render.render_iocontrol`,
        which reads this controller's state (and clamps ``_scroll_top``).
        """
        return render_iocontrol(self)

    def _apply_edit(self, did: str, field: str, value) -> None:
        """Persist an edit to ecus/<ecu>.yaml and refresh in-memory state.

        Errors are surfaced via ``self._status``; the original on-disk file
        is left intact when a write fails (surgical edits only mutate on
        successful regex match).
        """
        try:
            fpath = update_iocontrol_field(self.ecu_key, did, field, value, profile=self.profile)
        except PidsEditError as exc:
            self._status = ansi.c(f"{glyphs.FAIL} Edit failed: {exc}", ansi.RED)
            _tui_logger.error("edit %s %s=%r: %s", did, field, value, exc)
            return

        # Reload pids_data from disk and rebuild the iocontrol index so the
        # TUI reflects the change. We mutate the existing dict in place so
        # any references held elsewhere stay valid. Preserve discoveries so
        # the current view-mode filter keeps working.
        try:
            fresh = load_pids(profile=self.profile)
            self.pids_data.clear()
            self.pids_data.update(fresh)
            idx = build_iocontrol_index(self.pids_data, include_discoveries=True)
            self.all_cmds = idx[self.ecu_key]["cmds"]
            self._apply_view_filter()
            for d in self.dids:
                self.state.setdefault(d, None)
                self.status_bytes.setdefault(d, None)
        except Exception as exc:  # reload failure is non-fatal; in-memory stale
            _tui_logger.warning("reload after edit failed: %s", exc)

        saved = f"{glyphs.OK} Saved {field} on {did} {glyphs.ARROW} {fpath.name}"
        self._status = ansi.c(saved, ansi.GREEN)
        _tui_logger.info("edit %s %s=%r -> %s", did, field, value, fpath)

    def _apply_promote(self, did: str, label: str) -> None:
        """Promote a discovery DID to a curated entry and reload state.

        ``on``/``off`` are inferred from the discovery's captured response
        length (see ``yaml_edit.pids.promote_discovery``). The user refines the
        payload afterwards via ``e``/direct YAML edit if needed.
        """
        label = label.strip()
        if not label:
            self._status = ansi.c(f"{glyphs.FAIL} Promote cancelled: empty label", ansi.RED)
            return
        try:
            fpath = promote_discovery(self.ecu_key, did, label, profile=self.profile)
        except PidsEditError as exc:
            self._status = ansi.c(f"{glyphs.FAIL} Promote failed: {exc}", ansi.RED)
            _tui_logger.error("promote %s label=%r: %s", did, label, exc)
            return

        # Reload from disk so the curated entry (with inferred on/off) is
        # picked up and the discovery disappears from the discoveries view.
        try:
            fresh = load_pids(profile=self.profile)
            self.pids_data.clear()
            self.pids_data.update(fresh)
            idx = build_iocontrol_index(self.pids_data, include_discoveries=True)
            self.all_cmds = idx[self.ecu_key]["cmds"]
            self._apply_view_filter()
            for d in self.dids:
                self.state.setdefault(d, None)
                self.status_bytes.setdefault(d, None)
            # Keep cursor on the same DID if still visible, else clamp.
            if did in self.dids:
                self.cursor = self.dids.index(did)
            else:
                self.cursor = min(self.cursor, max(0, len(self.dids) - 1))
        except Exception as exc:
            _tui_logger.warning("reload after promote failed: %s", exc)

        promoted = (
            f"{glyphs.OK} Promoted {did} {glyphs.ARROW} curated (on/off inferred) "
            f"{glyphs.EMPTY} verify & refine via 'e'"
        )
        self._status = ansi.c(promoted, ansi.GREEN)
        _tui_logger.info("promote %s label=%r -> %s", did, label, fpath)

    def _draw(self) -> None:
        """Clear screen and draw the full frame."""
        sys.stdout.write(ansi.screen.CLEAR + ansi.screen.HOME)
        sys.stdout.write(self._render())
        sys.stdout.write("\n")
        sys.stdout.flush()

    async def run(self) -> None:
        """Main TUI event loop."""
        if not self.dids:
            messages.fail(f"no IOControl DIDs available for {self.ecu_key}")
        if not is_interactive():
            messages.fail(
                "the IOControl view needs a terminal; use `canair io ECU --json` to list DIDs "
                "or `canair io ECU --did DID` to send one",
                code=2,
            )

        # Set up debug log file (cleared each session)
        log_file = _log_file()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, mode="w")
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        _tui_logger.addHandler(handler)
        _tui_logger.setLevel(logging.DEBUG)
        _tui_logger.info(
            "TUI session start — %s (0x%03X), %d DIDs", self.ecu_key, self.tx_id, len(self.dids)
        )

        # Set up raw terminal for keypress reading
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        # Key input queue — filled by stdin reader
        key_queue: asyncio.Queue[str] = asyncio.Queue()

        def _on_stdin_ready() -> None:
            """Read keypress from raw stdin, enqueue it."""
            try:
                ch = os.read(fd, 16).decode("utf-8", errors="ignore")
                key_queue.put_nowait(ch)
            except Exception:
                pass

        loop = asyncio.get_event_loop()

        # Enter alternate screen buffer for clean rendering
        sys.stdout.write(ansi.screen.ALT_SCREEN_ON + ansi.screen.HIDE_CURSOR)
        sys.stdout.flush()

        try:
            tty.setcbreak(fd)  # cbreak instead of raw — allows signal handling
            loop.add_reader(fd, _on_stdin_ready)

            # Start background status polling — ONLY when opted in via --poll. The poll sends
            # each DID's release (2F {DID} 00 on UDS), which is NOT a guaranteed silent read:
            # returnControlToECU hands the I/O back to the ECU's own logic and can physically
            # actuate relay/solenoid-backed DIDs (e.g. IGPM door lock/unlock, trunk, charge
            # cable lock, defogger), with an audible click on the first poll.
            poll_task = (
                asyncio.ensure_future(self.actuator.status_poll_loop())
                if self._poll_enabled
                else None
            )

            self._draw()

            while not self._quit:
                # Wait for keypress with timeout (allows periodic redraw)
                try:
                    key = await asyncio.wait_for(key_queue.get(), timeout=0.5)
                except TimeoutError:
                    self._draw()
                    continue

                if key == "\x03":  # Ctrl+C — always quits (even mid-edit)
                    if self._hex_input is not None:
                        self._hex_input = None
                    elif self._edit_input is not None:
                        self._edit_input = None
                    else:
                        self._status = f"Releasing actuators{glyphs.ELLIPSIS}"
                        self._draw()
                        self._quit = True
                elif key in ("q", "Q") and self._hex_input is None and self._edit_input is None:
                    # q only quits in nav mode — allow it as a typed char in edits.
                    self._status = f"Releasing actuators{glyphs.ELLIPSIS}"
                    self._draw()
                    self._quit = True
                elif self._edit_input is not None:
                    # Metadata-edit input mode — capture any printable text.
                    field, buf = self._edit_input
                    if key == "\x1b":  # Esc — cancel
                        self._edit_input = None
                    elif key in ("\r", "\n"):  # Enter — save
                        self._edit_input = None
                        did = self.dids[self.cursor]
                        if field == "promote":
                            self._apply_promote(did, buf)
                        else:
                            self._apply_edit(did, field, buf)
                    elif key in ("\x7f", "\x08"):  # Backspace
                        self._edit_input = (field, buf[:-1])
                    elif len(key) == 1 and (key.isprintable() or key == "\t"):
                        self._edit_input = (field, buf + key)
                    # Ignore other control sequences while editing metadata.
                elif self._hex_input is not None:
                    # Hex input mode — capture hex chars, backspace, enter, escape
                    if key == "\x1b":  # Escape — cancel input
                        self._hex_input = None
                    elif key in ("\r", "\n"):  # Enter — send value
                        hex_str = self._hex_input.strip()
                        self._hex_input = None
                        if hex_str and len(hex_str) % 2 == 0 and not self._busy:
                            did = self.dids[self.cursor]
                            value_bytes = bytes.fromhex(hex_str)
                            await self.actuator.send_adjust(did, value_bytes)
                        elif hex_str:
                            self._status = ansi.c(
                                "Invalid: need an even number of hex chars", ansi.YELLOW
                            )
                    elif key in ("\x7f", "\x08"):  # Backspace/Delete
                        self._hex_input = self._hex_input[:-1]
                    elif len(key) == 1 and key.lower() in "0123456789abcdef":
                        self._hex_input += key.upper()
                    # Ignore other keys in hex input mode
                elif key in ("\x1b[A", "k"):  # Up arrow or k
                    self.cursor = max(0, self.cursor - 1)
                elif key in ("\x1b[B", "j"):  # Down arrow or j
                    self.cursor = min(len(self.dids) - 1, self.cursor + 1)
                elif key in ("\x1b[5~", "\x02"):  # PageUp / Ctrl+B
                    page = max(1, _terminal_lines() - 10)
                    self.cursor = max(0, self.cursor - page)
                elif key in ("\x1b[6~", "\x06"):  # PageDown / Ctrl+F
                    page = max(1, _terminal_lines() - 10)
                    self.cursor = min(len(self.dids) - 1, self.cursor + page)
                elif key in ("\x1b[H", "g"):  # Home / g — jump to top
                    self.cursor = 0
                elif key in ("\x1b[F", "G"):  # End / G — jump to bottom
                    self.cursor = len(self.dids) - 1
                elif key in ("\r", "\n", " "):  # Enter or Space — toggle ON/OFF
                    if not self._busy:
                        did = self.dids[self.cursor]
                        await self.actuator.toggle(did)
                elif key in ("o", "O"):  # Explicit OFF
                    if not self._busy:
                        did = self.dids[self.cursor]
                        await self.actuator.send_off(did)
                elif key in ("v", "V"):  # Enter hex value input mode
                    if not self._busy:
                        did = self.dids[self.cursor]
                        refusal = self.actuator.adjust_unsupported(did)
                        if refusal:
                            self._status = refusal
                        else:
                            # Seed with last value if available, otherwise blank
                            seed = (
                                self.last_value[did].hex().upper() if did in self.last_value else ""
                            )
                            self._hex_input = seed
                elif key == "+" and not self._busy:  # Increment last value
                    did = self.dids[self.cursor]
                    if did in self.last_value:
                        val = int.from_bytes(self.last_value[did], "big") + 1
                        n = len(self.last_value[did])
                        if val < (1 << (8 * n)):
                            await self.actuator.send_adjust(did, val.to_bytes(n, "big"))
                elif key == "-" and not self._busy:  # Decrement last value
                    did = self.dids[self.cursor]
                    if did in self.last_value:
                        val = int.from_bytes(self.last_value[did], "big") - 1
                        n = len(self.last_value[did])
                        if val >= 0:
                            await self.actuator.send_adjust(did, val.to_bytes(n, "big"))
                elif key == "e" and not self._busy:  # Edit label
                    did = self.dids[self.cursor]
                    self._edit_input = ("label", self.cmds[did].get("label", ""))
                elif key == "n" and not self._busy:  # Edit notes
                    did = self.dids[self.cursor]
                    current = (self.cmds[did].get("notes") or "").strip()
                    self._edit_input = ("notes", current)
                elif key == "m" and not self._busy:  # Toggle verified
                    did = self.dids[self.cursor]
                    new_val = not bool(self.cmds[did].get("verified", False))
                    self._apply_edit(did, "verified", new_val)
                elif key == "d" and not self._busy:  # Cycle view (curated/all/discoveries)
                    self._cycle_view()
                elif key == "P" and not self._busy:  # Promote discovery
                    did = self.dids[self.cursor]
                    if not self.cmds[did].get("discovery"):
                        self._status = ansi.c(
                            f"Not a discovery {glyphs.EMPTY} use 'e' to edit curated entries",
                            ansi.YELLOW,
                        )
                    else:
                        self._edit_input = ("promote", "")

                self._draw()

        finally:
            loop.remove_reader(fd)
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            # Cancel background poll task
            if poll_task is not None:
                poll_task.cancel()
                try:
                    await poll_task
                except asyncio.CancelledError:
                    pass
            # Capture final render before leaving alternate screen
            final_render = self._render()
            # Leave alternate screen buffer
            sys.stdout.write(ansi.screen.SHOW_CURSOR + ansi.screen.ALT_SCREEN_OFF)
            sys.stdout.flush()
            # Print final state to main screen
            print(final_render)
            await self.actuator.cleanup()
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
):
    """Interactive IOControl TUI for an ECU."""
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

    tui = _IOControlTUI(terminal, ecu_key, ecu_info, pids_data, verbose, poll, profile=profile)
    await tui.run()
