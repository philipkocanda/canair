"""Live monitor — ELM327 polling backend.

:class:`MonitorElmPoller` is the collaborator that opens the sessions on an ELM327
terminal and runs one poll cycle over it: every ECU step is read through the shared
step executor, each PID's cost is left for the health check, and the screen is
repainted mid-cycle so a slow PID does not freeze the view. It holds the multi-DID
batching state (``batch_state``) of the run.

The raw ``slcan-tcp`` backend is :class:`~canair.live.monitor.raw_poller.MonitorRawPoller`.
Callers read this one as ``controller.elm_poller``. Each exchange runs inside the step
executor, which holds ``terminal.transaction()`` across ``set_header`` and ``send_uds``.
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING

from canair.live.read import multi
from canair.live.read.batch import BatchState
from canair.live.read.exec_read import timed_exchange
from canair.transport.isotp_params import resolve_rx_padding
from canair.uds.echo import request_echo

from .health import pid_costs

if TYPE_CHECKING:
    from .controller import MonitorController

# The shortest gap between two mid-cycle repaints, in seconds.
_PARTIAL_REPAINT_S = 0.12


class MonitorElmPoller:
    """ELM327 session setup and poll cycle for one monitor run."""

    def __init__(self, controller: MonitorController):
        self.c = controller
        self.batch_state: BatchState | None = None  # created in setup()

    async def setup(self, session_steps: list[dict] | None) -> None:
        """Open the requested sessions, start keepalives and prime each ECU once."""
        c = self.c
        assert c.sm is not None  # not c.raw ⟺ sm was constructed
        assert c._ecu_index is not None  # built by MonitorController.setup
        self.batch_state = BatchState(resolve_rx_padding(c.pids_data))
        for step in session_steps or []:
            stype = step["type"]
            if stype == "skm-wake":
                print(f"  SKM wakeup ({step['level']})...")
                await multi._exec_skm_wake(c.sm, step["level"], c.verbose)
            elif stype == "session":
                print(f"  Opening session on {step['target']}...")
                await multi._exec_session(
                    c.sm,
                    step["target"],
                    step.get("wake", False),
                    c._ecu_index,
                    mode=step.get("mode", "03"),
                    profile=self.c.profile,
                )
        c.sm.start_background_keepalive(interval=2.0)
        # Prime each ECU once (parity with the raw path): the first request after
        # idle is slow, so warm the path before the first *displayed* cycle. The
        # retry (retries=1) also rides out a cold NO-DATA on this throwaway read.
        for _ecu, info, request in c.sessions.warmup_reads():
            echo = request_echo(request)
            with contextlib.suppress(Exception):
                # One transaction, echo-validated: the background keepalive is already
                # running, and could otherwise re-point the header between the two.
                await timed_exchange(
                    c.sm,
                    info["tx_id"],
                    request,
                    retries=1,
                    expected_sid=echo[0] if echo else None,
                    expected_echo=echo[1] if echo else None,
                )

    async def poll(self) -> None:
        """Read every due PID of every ECU step, updating the controller's live state."""
        c = self.c
        # The ELM poll path only runs on the non-raw backend, where setup() has
        # constructed sm and built the ECU index.
        assert c.sm is not None
        assert c._ecu_index is not None
        cmds0 = c.terminal.cmd_count
        elm0 = c.terminal.cmd_time
        # Seed the frame from last cycle so ECUs not yet re-polled keep showing
        # their values (no flicker); overwrite each as it completes this cycle.
        frame = dict(c.last_queries)
        order = [label for label, _ in c.last_queries]
        polled: list[str] = []
        last_render = 0.0
        for step in c.query_steps:
            full_codes: list[str] = []
            due_on: list[str] = []

            def _take_due(
                ecu: str, plan: list, codes: list[str] = full_codes, due_on: list[str] = due_on
            ) -> list:
                codes.extend(entry[0] for entry in plan)
                due = c.schedule.take_due(ecu, plan)
                if due:
                    due_on.append(ecu)
                return due

            try:
                result = await multi._exec_query(
                    c.sm,
                    step["ecu"],
                    step.get("pids", []),
                    c._ecu_index,
                    c.pids_data,
                    c.verbose,
                    return_results=True,
                    quiet=True,
                    batch_state=self.batch_state,
                    include_static=c.include_static,
                    plan_filter=_take_due,
                    profile=self.c.profile,
                )
            except ConnectionError:
                c.mark_disconnected("error")
                return
            if result is not None and due_on:
                c.health.poll_costs.update(pid_costs(due_on[0], result[1]))
            if result is not None:
                label, pids = result
                # Keep a timed-out PID's last-good values (stale/dimmed) instead
                # of collapsing to an error line and jolting the layout.
                pids = [c._displayify((label, e["pid"]), e) for e in pids]
                if pids:
                    c.fresh_queries.append((label, pids))
                if label not in frame:
                    order.append(label)
                if label not in polled:
                    polled.append(label)
                # Every PID of the plan, in plan order: the ones read this tick
                # with their new result, the rest as they were last shown.
                frame[label] = [
                    c._last_shown[(label, code)]
                    for code in full_codes
                    if (label, code) in c._last_shown
                ]
                c.last_queries = [(lb, frame[lb]) for lb in order if lb in frame]
                now = time.monotonic()
                if c.on_partial is not None and (now - last_render) >= _PARTIAL_REPAINT_S:
                    last_render = now
                    with contextlib.suppress(Exception):
                        c.on_partial()
        c.last_queries = [(lb, frame[lb]) for lb in polled]
        c.last_cmds = c.terminal.cmd_count - cmds0
        c.last_elm_time = c.terminal.cmd_time - elm0
