"""Handlers for the memory reads: ``canair scan memory`` and ``canair memory dump``.

Both confirm in the command, before connecting; a handler only reads the flags and runs.
"""

from __future__ import annotations

from canair.cli.commands.memory.render import print_dump_summary, print_scan
from canair.cli.live.operation import LiveOperation
from canair.core.skip_key import skip_listener
from canair.install.build_info import full_version
from canair.live.memory.dump import mode_memory_dump
from canair.live.memory.reads import MemoryTargetError, resolve_target
from canair.live.memory.scan import mode_memory_scan
from canair.memory.store import store_root
from canair.profile import active
from canair.render import messages
from canair.transport.protocol import Terminal
from canair.transport.stats import client_transport
from canair.uds.memory import parse_format
from canair.variants.tags import selected_variant


def _target(args, pids_data):
    try:
        return resolve_target(pids_data, args.ecu)
    except MemoryTargetError as e:
        messages.fail(str(e), code=2)


def _session(args, target) -> str | None:
    if args.session:
        return args.session_mode or (target.memory.session if target.memory else None) or "03"
    return target.memory.session if target.memory else None


async def handle_memory_scan(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    target = _target(args, pids_data)
    scan = await mode_memory_scan(
        terminal,
        target,
        list(args.ranges),
        step=args.step,
        fmt=parse_format(args.format) if args.format else None,
        session_mode=_session(args, target),
        throttle_ms=args.throttle_ms,
        find_block=not args.no_block,
        block_limit=args.block_limit,
        write_yaml=not args.dry_run,
    )
    print_scan(scan, as_json=args.json)


async def handle_memory_dump(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    target = _target(args, pids_data)
    prof = active()
    provenance = {
        "profile": prof.name,
        "variant": selected_variant(prof),
        "revisions": dict(prof.revisions),
        "canair": full_version(),
        "transport": client_transport(terminal),
    }
    with skip_listener(not args.json) as skip:
        if skip.active:
            messages.note("press s or escape to stop the dump and keep what was read")
        try:
            run = await mode_memory_dump(
                terminal,
                target,
                list(args.ranges),
                store_root(prof.name),
                fmt=parse_format(args.format) if args.format else None,
                block=args.block,
                session_mode=_session(args, target),
                label=args.dump_label,
                throttle_ms=args.throttle_ms,
                skip=lambda: skip.pressed,
                provenance=provenance,
            )
        except ValueError as e:
            messages.fail(str(e), code=2)
    note = f"dump ended early: {run.stopped}" if run.stopped else None
    print_dump_summary(run.dump, as_json=args.json, note=note)


SCAN = LiveOperation(handle_memory_scan)
DUMP = LiveOperation(handle_memory_dump)
