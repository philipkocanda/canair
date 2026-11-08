"""Is the ECU still the one, still answering, and is the car still in the state the run needs?

``canair memory image`` reads for a long time, unattended. Between extents it checks, in
the same session and without a reconnect:

- **health** — the ECU answers its health PID (by default its first active PID);
- **state** — the vehicle state the user named still holds: its ``when:`` predicate is
  evaluated on the signals it reads (:mod:`canair.states`);
- **liveness** — one byte at an address known to answer.

When a check fails, or the link drops, the run pauses: it polls gently for a bounded time
(:func:`wait_until_ready`) and goes on by itself when every check passes again. canair never
acts on the car to restore a state: it waits, or it saves the job for a later resume.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from canair.ecus.memory import protocol_of
from canair.ecus.pid_status import pid_status
from canair.expression.decoding import decode_param_rows
from canair.live.identity.software import read_software_version
from canair.states.predicates import StateRule, predicate_references
from canair.transport.errors import transport_error_types
from canair.transport.protocol import Terminal
from canair.uds.echo import request_echo
from canair.uds.memory import RegionResult, is_memory_request, memory_read
from canair.uds.response import UdsResponse

from .reads import MemoryTarget, classify, read_memory

#: The gap between two liveness polls while a run waits.
POLL_S = 30.0


@dataclass(frozen=True)
class SignalRead:
    """One PID read for a check, and the signals decoded from it."""

    ecu: str
    tx_id: int
    pid: str
    parameters: Mapping[str, Any] = field(default_factory=dict)


def health_read(ecu_def: Mapping[str, Any], name: str, pid: str | None = None) -> SignalRead:
    """The ECU's health read: ``pid``, else its first active PID that is not a memory read.

    Raises :class:`ValueError` for a ``pid`` the ECU does not define.
    """
    pids = {str(k).upper(): v for k, v in (ecu_def.get("pids") or {}).items()}
    if pid is not None:
        wanted = pid.upper()
        if wanted not in pids:
            raise ValueError(f"{name} defines no PID {pid}")
        return SignalRead(name, int(ecu_def["tx_id"]), wanted)
    for code, pid_def in pids.items():
        if pid_status(pid_def) == "active" and not is_memory_request(code):
            return SignalRead(name, int(ecu_def["tx_id"]), code)
    raise ValueError(f"{name} has no active PID to check its health with: pass --health PID")


def state_reads(pids_data: Mapping[str, Any], rule: StateRule) -> list[SignalRead]:
    """The PIDs to read to evaluate ``rule`` (one per PID, with the signals it needs).

    Raises :class:`ValueError` for a reference no ECU defines, since the state could then
    never be confirmed.
    """
    ecus = {str(k).upper(): (k, v) for k, v in (pids_data.get("ecus") or {}).items()}
    out: dict[tuple[int, str], SignalRead] = {}
    for ref in predicate_references(rule.expr):
        if "." not in ref:
            continue
        ecu, _, param = ref.partition(".")
        name, ecu_def = ecus.get(ecu.upper(), (None, None))
        if ecu_def is None:
            raise ValueError(f"state {rule.name}: no ECU {ecu} for {ref}")
        found = next(
            (
                (str(code).upper(), p["parameters"][param])
                for code, p in (ecu_def.get("pids") or {}).items()
                if pid_status(p) != "ignored" and param in (p.get("parameters") or {})
            ),
            None,
        )
        if found is None:
            raise ValueError(f"state {rule.name}: {name} defines no signal {param}")
        code, param_def = found
        key = (int(ecu_def["tx_id"]), code)
        prior = out.get(key)
        params = {**(prior.parameters if prior else {}), param: param_def}
        out[key] = SignalRead(str(name).upper(), key[0], code, params)
    return list(out.values())


async def read_signal(terminal: Terminal, read: SignalRead) -> UdsResponse:
    """One echo-validated read of ``read``'s PID."""
    echo = request_echo(read.pid)
    async with terminal.transaction():
        await terminal.set_header(read.tx_id)
        return await terminal.send_uds(
            read.pid,
            expected_sid=echo[0] if echo else None,
            expected_echo=echo[1] if echo else None,
            retries=1,
        )


@dataclass(frozen=True)
class Conditions:
    """What must hold while an image is read."""

    health: SignalRead | None = None
    state: StateRule | None = None
    state_reads: tuple[SignalRead, ...] = ()


async def unmet(terminal: Terminal, conditions: Conditions) -> str | None:
    """Why ``conditions`` do not hold now, or ``None`` when they do."""
    if conditions.health is not None:
        resp = await read_signal(terminal, conditions.health)
        if not resp.get("ok"):
            why = resp.get("nrc_desc") or resp.get("error") or "no answer"
            return f"the health read {conditions.health.pid} failed ({why})"
    rule = conditions.state
    if rule is None or rule.predicate is None:
        return None
    values: dict[str, float] = {}
    responded: set[str] = set()
    for read in conditions.state_reads:
        resp = await read_signal(terminal, read)
        if not resp.get("ok"):
            continue
        responded.add(read.ecu)
        for row in decode_param_rows(resp["hex"], dict(read.parameters)):
            if isinstance(row[1], (int, float)):
                values[f"{read.ecu}.{row[0]}"] = row[1]
    verdict = rule.predicate(values, responded)
    if verdict is True:
        return None
    if verdict is False:
        return f"the car is no longer {rule.name}"
    return f"{rule.name} cannot be confirmed (its signals did not answer)"


async def answers_at(terminal: Terminal, target: MemoryTarget, address: int) -> bool:
    """Does a 1-byte memory read at ``address`` get an answer?"""
    assert target.format is not None
    resp = await read_memory(terminal, target.tx_id, memory_read(target.format, address, 1))
    return classify(resp) is not RegionResult.NO_ANSWER


class Link:
    """The terminal a run reads through, swapped by a mid-session reconnect.

    Shaped like the monitor's controller (``close_client``, ``rebind``, ``setup``), so the
    monitor's reconnector re-homes it: a run has one reconnect path, not two.
    """

    def __init__(self, terminal: Terminal, reconnect: Callable[..., Awaitable[bool]] | None):
        self.terminal = terminal
        self._reconnect = reconnect
        self.dropped = False

    async def close_client(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.terminal.close(), timeout=2.0)

    def rebind(self, client: Terminal) -> None:
        self.terminal = client

    async def setup(self, _steps: object) -> None:
        return None

    @property
    def can_reconnect(self) -> bool:
        return self._reconnect is not None

    async def recover(self, notice: Callable[[str], None]) -> bool:
        """Re-home a dropped link; False when there is no reconnect or it did not work."""
        if self._reconnect is None:
            return False
        if await self._reconnect(self, None, notice=notice):
            self.dropped = False
            return True
        return False


@dataclass
class Readiness:
    """Everything a run checks before it reads on: liveness, the conditions, the software."""

    target: MemoryTarget
    conditions: Conditions
    software: str | None
    alive_at: int
    make: str | None = None

    async def problem(self, terminal: Terminal) -> str | None:
        """Why the run cannot read on now, or ``None``. Raises on a changed software build."""
        if not await answers_at(terminal, self.target, self.alive_at):
            return "the ECU does not answer"
        if (why := await unmet(terminal, self.conditions)) is not None:
            return why
        software = await read_software_version(
            terminal, self.target.tx_id, protocol_of(self.target.ecu_def), make=self.make
        )
        if self.software and software and software != self.software:
            raise SoftwareChanged(self.software, software)
        return None


class SoftwareChanged(Exception):
    """The ECU reports another software build than the job's: reflashed, or another ECU."""

    def __init__(self, expected: str, found: str):
        super().__init__(
            f"the ECU reports software {found}, the job was read from {expected}: it was "
            "reflashed, or this is another ECU"
        )


async def wait_until_ready(
    link: Link,
    readiness: Readiness,
    *,
    wait_s: float,
    poll_s: float = POLL_S,
    notice: Callable[[str], None] = lambda _m: None,
    stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Poll every ``poll_s`` for at most ``wait_s``; True once every check passes again.

    One liveness read (and the checks) per poll: an ECU that may be hung is never asked
    for more. A dropped link is re-homed first; without a reconnect there is nothing to
    wait for, and the run is saved at once.
    """
    if link.dropped and not link.can_reconnect:
        return False
    deadline = clock() + wait_s
    while clock() < deadline and not stop():
        await sleep(min(poll_s, max(0.0, deadline - clock())))
        if stop():
            return False
        if link.dropped and not await link.recover(notice):
            continue
        try:
            why = await readiness.problem(link.terminal)
        except transport_error_types():
            link.dropped = True
            if not link.can_reconnect:
                return False
            continue
        if why is None:
            return True
        notice(f"still waiting: {why}")
    return False
