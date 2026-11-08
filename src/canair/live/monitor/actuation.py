"""Live monitor — the actuators of ``--actuate``, driven from the monitor's actuator pane.

:class:`MonitorActuation` (``controller.actuation``) holds one
:class:`~canair.live.actuate.iocontrol_actuate.IoBank` and one
:class:`~canair.live.actuate.routine_bank.RoutineBank` per ECU that ``--actuate`` names, and
puts their requests on the monitor's foreground lane (:mod:`.foreground`), which shares the bus
with the poll request by request. It never imports Textual: the pane
(``tui/apps/monitor/actuators.py``) reads :meth:`MonitorActuation.actuators` and calls the
``submit_*`` methods.

Sessions stay the monitor's: a request opens its entry's session mode on the monitor's own
``SessionManager``, which keeps it alive with the rest. When the mode differs from the one the
ECU had, the ECU switches, and once nothing on that ECU is held any more (no actuator ON, no
routine started and not stopped) the previous mode comes back, or the default session when
the ECU had none. The live session switch refuses an ECU that holds something
(:meth:`MonitorActuation.switch_refusal`).

With ``--save``, every request is journaled as one of the session's ``actuations``
(:mod:`canair.captures.actuations`), and a new segment (``n``) starts with a ``held`` entry for
each output still on.

Every actuator still ON is released when the monitor exits (:meth:`MonitorActuation.release_all`),
and again after a reconnect (:meth:`MonitorActuation.after_reconnect`): the dropped link may or
may not have ended the ECU's session, and a release is safe to repeat.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from canair.captures.actuations import HELD, ActuationAction, ActuationOutcome, actuation_record
from canair.ecus.names import canonical_ecu_name_safe
from canair.ecus.pids import build_iocontrol_index, build_routines_index
from canair.live.actuate.iocontrol_actuate import ACTUATOR_ON, IoBank
from canair.live.actuate.ports import SharedSessions, StatusLevel
from canair.live.actuate.routine_bank import RoutineBank
from canair.live.actuate.routines_execute import routines_refusal
from canair.profile.model import Profile
from canair.uds.response import UdsResponse
from canair.uds.subfunctions import RoutineControlType

if TYPE_CHECKING:
    from .controller import MonitorController

_logger = logging.getLogger(__name__)

ActuatorKind = Literal["iocontrol", "routine"]


@dataclass(frozen=True)
class Actuator:
    """One row of the actuator pane: an IOControl DID or a routine of one ECU."""

    key: str  # "ECU/iocontrol/DID": unique across the pane
    ecu: str
    kind: ActuatorKind
    ident: str  # the DID or the RID
    label: str
    verified: bool
    status_param: str | None = None


def actuate_ecus(values: Sequence[str]) -> list[str]:
    """``--actuate`` values (repeatable, each ``ECU[,ECU…]``) as upper-case names, in order."""
    out: list[str] = []
    for value in values:
        for name in value.split(","):
            name = name.strip().upper()
            if name and name not in out:
                out.append(name)
    return out


def actuate_problem(pids_data: dict, ecus: Sequence[str], *, profile: Profile) -> str | None:
    """Why ``--actuate ecus`` cannot be honoured, or None.

    Each ECU must exist (an alias resolves) and have curated ``iocontrol:`` entries or
    ``routines:`` a UDS ECU can run.
    """
    io = build_iocontrol_index(pids_data)
    routines = build_routines_index(pids_data)
    known = {name.upper() for name in pids_data.get("ecus", {})}
    for name in ecus:
        ecu = canonical_ecu_name_safe(name, None, profile=profile).upper()
        if ecu not in known:
            return f"--actuate {name}: no such ECU in the profile"
        runnable = ecu in routines and routines_refusal(pids_data, ecu) is None
        if ecu not in io and not runnable:
            return (
                f"--actuate {name}: {ecu} has no iocontrol: entries and no routines: a UDS ECU "
                "can run"
            )
    return None


class _MonitorSessions:
    """The banks' session port: the monitor's manager, remembering each ECU's first mode."""

    def __init__(self, actuation: MonitorActuation) -> None:
        self._a = actuation
        self._shared = SharedSessions(lambda: actuation.c.sm)

    async def ensure(self, tx_id: int, mode: str | None) -> bool:
        self._a.remember_mode(tx_id)
        return await self._shared.ensure(tx_id, mode)


class MonitorActuation:
    """The ``--actuate`` ECUs' actuators and routines, and their requests on the lane."""

    def __init__(self, controller: MonitorController, ecus: Sequence[str]) -> None:
        self.c = controller
        pids_data = controller.pids_data
        profile = controller.profile
        io_index = build_iocontrol_index(pids_data)
        r_index = build_routines_index(pids_data)
        sessions = _MonitorSessions(self)
        self.io: dict[str, IoBank] = {}
        self.routines: dict[str, RoutineBank] = {}
        #: Why an ECU's routines are left out (a KWP2000 ECU runs any 0x31 request).
        self.notes: list[str] = []
        for name in ecus:
            ecu = canonical_ecu_name_safe(name, None, profile=profile).upper()
            if ecu in io_index:
                entry = io_index[ecu]
                self.io[ecu] = IoBank(
                    lambda: controller.sm.terminal,
                    entry["tx_id"],
                    entry["protocol"],
                    entry["cmds"],
                    sessions,
                    self.report,
                )
                self.io[ecu].on_exchange = self._recorder(ecu, "iocontrol")
            if ecu in r_index:
                refusal = routines_refusal(pids_data, ecu)
                if refusal is not None:
                    self.notes.append(refusal.splitlines()[0] + ": its routines are left out")
                    continue
                entry = r_index[ecu]
                self.routines[ecu] = RoutineBank(
                    lambda: controller.sm.terminal,
                    entry["tx_id"],
                    entry["routines"],
                    sessions,
                    self.report,
                )
                self.routines[ecu].on_exchange = self._recorder(ecu, "routine")
        # The session mode each ECU had before the first actuation (None: no session), to
        # restore once nothing on it is held.
        self._before: dict[int, int | None] = {}
        #: Keys of the actuators with a request queued or on the bus.
        self.pending: set[str] = set()
        # The latest message for the user; the pane shows it and clears it.
        self.status = ""
        self.status_level: StatusLevel = "info"

    # -- what the pane shows ---------------------------------------------------
    def actuators(self) -> list[Actuator]:
        """Every row, ECU by ECU in ``--actuate`` order: IOControl DIDs, then routines."""
        out: list[Actuator] = []
        for ecu in dict.fromkeys([*self.io, *self.routines]):
            bank = self.io.get(ecu)
            for did, cmd in (bank.all_cmds if bank else {}).items():
                out.append(
                    Actuator(
                        f"{ecu}/iocontrol/{did}",
                        ecu,
                        "iocontrol",
                        did,
                        cmd.get("label") or did,
                        bool(cmd.get("verified")),
                        cmd.get("status_param"),
                    )
                )
            rbank = self.routines.get(ecu)
            for rid, rdef in (rbank.routines if rbank else {}).items():
                out.append(
                    Actuator(
                        f"{ecu}/routine/{rid}",
                        ecu,
                        "routine",
                        rid,
                        rdef.get("label") or rid,
                        bool(rdef.get("verified")),
                    )
                )
        return out

    def actuator(self, key: str) -> Actuator | None:
        return next((a for a in self.actuators() if a.key == key), None)

    def io_bank(self, a: Actuator) -> IoBank:
        return self.io[a.ecu]

    def routine_bank(self, a: Actuator) -> RoutineBank:
        return self.routines[a.ecu]

    def on_count(self) -> int:
        """How many outputs this run switched on and has not released."""
        return sum(len(bank.active_dids()) for bank in self.io.values())

    def held(self, ecu: str) -> list[str]:
        """What ``ecu`` holds: its actuators that are ON, and its routines started and not
        stopped (they may still run)."""
        bank = self.io.get(ecu)
        out = list(bank.active_dids()) if bank else []
        rbank = self.routines.get(ecu)
        if rbank is not None:
            for rid in rbank.routines:
                started = rbank.last_sf.get(rid) == RoutineControlType.START_ROUTINE
                if started and rbank.state.get(rid) == "ok":
                    out.append(rid)
        return out

    def switch_refusal(self, ecu: str) -> str | None:
        """Why the live session switch must leave ``ecu`` alone, or None."""
        held = self.held(ecu.upper())
        if not held:
            return None
        return (
            f"{ecu.upper()} holds {', '.join(held)}: release it first (a session switch would "
            "hand it back to the ECU)"
        )

    # -- messages ----------------------------------------------------------------
    def report(self, text: str, level: StatusLevel = "info") -> None:
        self.status, self.status_level = text, level

    def take_status(self) -> tuple[str, StatusLevel] | None:
        """The pending warning or error, once (an in-flight "Sending …" is not one)."""
        if not self.status or self.status_level == "info":
            return None
        out = (self.status, self.status_level)
        self.report("")
        return out

    # -- recording -----------------------------------------------------------------
    def _recorder(self, ecu: str, kind: Literal["iocontrol", "routine"]):
        """The bank hook that journals each request of ``ecu`` (when ``--save`` records)."""

        def record(
            action: ActuationAction,
            ident: str,
            request: str,
            resp: UdsResponse | None,
            at: datetime,
        ) -> None:
            journal = self.c.recorder.journal
            rx = self.c._event_rx(ecu)
            if journal is None or rx is None:
                return
            outcome: ActuationOutcome = "error"
            if resp is not None and resp.get("ok"):
                outcome = "ok"
            elif resp is not None and resp.get("nrc") is not None:
                outcome = "nrc"
            journal.append_actuation(
                actuation_record(
                    at,
                    rx,
                    kind,
                    ident,
                    action,
                    request=request,
                    outcome=outcome,
                    response=resp.get("hex") if resp is not None else None,
                    nrc=resp.get("nrc") if resp is not None else None,
                )
            )

        return record

    def journal_held(self) -> None:
        """Start a new segment's journal with a ``held`` entry per output still on."""
        journal = self.c.recorder.journal
        if journal is None:
            return
        now = datetime.now()
        for ecu, bank in self.io.items():
            rx = self.c._event_rx(ecu)
            if rx is None:
                continue
            for did in bank.active_dids():
                journal.append_actuation(actuation_record(now, rx, "iocontrol", did, HELD))

    # -- sessions ----------------------------------------------------------------
    def remember_mode(self, tx_id: int) -> None:
        """Note ``tx_id``'s session mode before an actuation first touches it."""
        if tx_id not in self._before:
            self._before[tx_id] = self.c.sm.session_mode(tx_id)

    def forget_mode(self, tx_id: int) -> None:
        """The user chose ``tx_id``'s session (the live switch): do not restore it later."""
        self._before.pop(tx_id, None)

    async def _restore_if_idle(self, ecu: str, tx_id: int) -> None:
        if self.held(ecu) or tx_id not in self._before:
            return
        before = self._before.pop(tx_id)
        sm = self.c.sm
        if sm.session_mode(tx_id) == before:
            return
        if before is None:
            await sm.close_session(tx_id)
        else:
            await sm.open_session(tx_id, mode=before, force=True, report=False)

    # -- requests ----------------------------------------------------------------
    def _submit(
        self, a: Actuator, what: str, tx_id: int, make: Callable[[], Awaitable[object]]
    ) -> asyncio.Future:
        self.pending.add(a.key)

        async def job() -> object:
            try:
                result = await make()
                await self._restore_if_idle(a.ecu, tx_id)
                return result
            finally:
                self.pending.discard(a.key)

        return self.c.foreground.submit(f"{what} {a.ecu} {a.ident}", job)

    def value_seed(self, a: Actuator) -> str | None:
        """For an IOControl DID with no simple ON: the value to offer (it needs one), else None."""
        bank = self.io_bank(a)
        if bank.state.get(a.ident) == ACTUATOR_ON or bank.all_cmds[a.ident]["on"]:
            return None
        last = bank.last_value.get(a.ident)
        return last.hex().upper() if last else "00"

    def submit_toggle(self, a: Actuator) -> asyncio.Future:
        bank = self.io_bank(a)
        return self._submit(a, "toggle", bank.tx_id, lambda: bank.toggle(a.ident))

    def submit_off(self, a: Actuator) -> asyncio.Future:
        bank = self.io_bank(a)
        return self._submit(a, "OFF", bank.tx_id, lambda: bank.send_off(a.ident))

    def adjust_refusal(self, a: Actuator) -> str | None:
        return self.io_bank(a).adjust_unsupported(a.ident)

    def submit_adjust(self, a: Actuator, value: bytes) -> asyncio.Future:
        bank = self.io_bank(a)
        return self._submit(a, "adjust", bank.tx_id, lambda: bank.send_adjust(a.ident, value))

    def submit_routine(self, a: Actuator, sub_function: int) -> asyncio.Future:
        bank = self.routine_bank(a)
        return self._submit(a, "routine", bank.tx_id, lambda: bank.send(a.ident, sub_function))

    # -- exit and reconnect --------------------------------------------------------
    async def release_all(self) -> list[str]:
        """Release every actuator still ON (the monitor is exiting); what was released."""
        released: list[str] = []
        for ecu, bank in self.io.items():
            active = bank.active_dids()
            if active:
                await bank.release_all()
                released.extend(f"{ecu} {did}" for did in active)
        return released

    async def after_reconnect(self) -> None:
        """Hand back every output that was ON before the link dropped, before polling resumes.

        The reconnect replayed the session steps, so the modes noted before it mean nothing.
        """
        self._before.clear()
        released = await self.release_all()
        if released:
            self.report(f"Released after the reconnect: {', '.join(released)}", "warning")
