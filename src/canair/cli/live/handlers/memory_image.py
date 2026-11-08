"""Handler for ``canair memory image``: plan, confirm, read and report one ECU's memory image.

The command asks before it connects; this handler asks again once the plan is made, then
listens for the skip key only while it reads, so the two never compete for stdin.
"""

from __future__ import annotations

import sys
from contextlib import ExitStack
from dataclasses import replace

from canair.cli.commands.memory.image_report import print_plan, print_report, print_written
from canair.cli.live.operation import LiveOperation
from canair.cli.profile_context import active
from canair.core.prompt import confirm
from canair.core.skip_key import SkipListener, skip_listener
from canair.ecus.memory import EcuMemory
from canair.ecus.revisions import determined_revisions
from canair.install.build_info import full_version
from canair.live.memory.image import ImageOptions, ImageRun, mode_memory_image
from canair.live.memory.reads import MemoryTarget, MemoryTargetError, resolve_target
from canair.memory.job import SPARSE, THOROUGH, job_plan
from canair.memory.store import DumpError, resolve_dump, store_root
from canair.profile.names import make_of
from canair.render import messages
from canair.states.vocabulary import load_states
from canair.transport.elm327.terminal import Elm327Terminal
from canair.transport.protocol import Terminal
from canair.transport.stats import client_transport
from canair.uds.memory import parse_format
from canair.variants.tags import selected_variant


def _target(pids_data, ecu: str, fmt: str | None) -> MemoryTarget:
    try:
        target = resolve_target(pids_data, ecu)
    except MemoryTargetError as e:
        messages.fail(str(e), code=2)
    if fmt is None:
        return target
    memory = target.memory or EcuMemory(None)
    return replace(target, memory=replace(memory, format=parse_format(fmt)))


def _session(args, target: MemoryTarget) -> str | None:
    declared = target.memory.session if target.memory else None
    if args.session:
        return args.session_mode or declared or "03"
    return declared


async def handle_memory_image(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    prof = active()
    store = store_root(prof.name)
    resume = None
    if args.resume:
        try:
            resume = resolve_dump(store, args.resume)
        except DumpError as e:
            messages.fail(str(e), code=2)
    plan = job_plan(resume) if resume is not None else None
    target = _target(pids_data, resume.ecu if resume else args.ecu, args.format)
    state = args.required_state or (plan.state if plan else None)
    rule = None
    if state is not None:
        rule = next((r for r in load_states(prof) if r.name == state.upper()), None)
        if rule is None or rule.predicate is None:
            messages.fail(f"state {state!r} has no when: predicate in this profile", code=2)
    options = ImageOptions(
        mode=THOROUGH if args.thorough else SPARSE,
        ranges=tuple(args.ranges or ()),
        step=args.step,
        stride=args.stride,
        block_limit=args.block_limit,
        health_pid=args.health,
        health_every_s=args.health_every,
        wait_s=args.wait_minutes * 60,
        session_mode=_session(args, target),
        throttle_ms=args.throttle_ms,
        label=args.dump_label,
        digit=isinstance(terminal, Elm327Terminal),
        plan_only=args.plan_only,
        dry_run=args.dry_run,
    )
    provenance = {
        "profile": prof.name,
        "variant": selected_variant(prof),
        "revisions": determined_revisions(prof),
        "canair": full_version(),
        "transport": client_transport(terminal),
    }
    listening = SkipListener()
    with ExitStack() as stack:

        def listen() -> None:
            nonlocal listening
            listening = stack.enter_context(skip_listener(not args.json))
            if listening.active:
                messages.note("press s or escape to stop; the job is saved for --resume")

        def confirm_plan(run: ImageRun) -> bool:
            print_plan(run, sys.stderr)
            if not confirm("Read this plan?", yes=args.yes, danger=True):
                return False
            listen()
            return True

        if resume is not None:
            listen()
        try:
            run = await mode_memory_image(
                terminal,
                target,
                store,
                options,
                pids_data=pids_data,
                profile=prof,
                rule=rule,
                make=make_of(prof.name),
                provenance=provenance,
                confirm_plan=confirm_plan,
                resume=resume,
                reconnect=reconnect if options.session_mode is None else None,
                skip=lambda: listening.pressed,
                notice=messages.note,
            )
        except ValueError as e:
            messages.fail(str(e), code=2)
    if options.plan_only:
        if args.json:
            print_report(run, as_json=True)
        else:
            print_plan(run, sys.stdout)
            print_written(run)
        if run.stopped:
            messages.error(f"the plan could not be made: {run.stopped}")
        return
    if run.declined:
        raise SystemExit(1)
    print_report(run, as_json=args.json)
    if run.stopped:
        raise SystemExit(1)


IMAGE = LiveOperation(handle_memory_image)
