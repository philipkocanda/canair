"""Multi-ECU pipeline — per-step execution primitives.

The device-talking building blocks a multi pipeline (and the live monitor and
REPL) compose: opening sessions, reading a single PID or a batched multi-DID
group, resolving a query plan, and the ``query``/``raw``/``iocontrol``/``scan``
step executors. Orchestration (looping over steps, journaling) lives in
:mod:`canair.live.read.multi`; parsing in :mod:`canair.query.steps`.
"""

import asyncio
import logging
import re
from collections.abc import Callable

from canair.core.eventlog import log_event
from canair.core.id_ranges import parse_id_ranges
from canair.ecus.names import ecu_index_key
from canair.ecus.pids import EcuIndexEntry, PidIndexEntry, build_iocontrol_index
from canair.privacy.sensitive import MASK, request_hidden
from canair.profile.model import Profile
from canair.query import QueryError, inferred_request, pids_named_by
from canair.query.steps import parse_raw_spec, resolve_tx_id
from canair.render import messages
from canair.render.formatting import print_ecu_results, print_hexdump
from canair.safety.actuation import on_refusal
from canair.transport.elm327.terminal import Elm327Terminal
from canair.transport.session_manager import (
    DEFAULT_SESSION_MODE,
    SessionManager,
    SessionOutcome,
)
from canair.uds.describe import decode_uds_response
from canair.uds.subfunctions import format_session_type, parse_session_type

from ...ecus.names import resolve_tx
from ...ecus.registry import rx_addr_str
from ..actuate.iocontrol_actuate import send_iocontrol
from ..elm.skm_wakeup import SKM_SESSION_MODE, SKM_TX_ID, mode_skm_wakeup
from ..scan.presets import ServiceError, resolve_service
from ..scan.range import confirm_actuating_scan, mode_scan
from .batch import (
    BatchState,
    _did4,
    _is_did22,
    transport_did_cap,
)
from .exec_read import _read_batch, _read_single


async def exec_skm_wake(sm: SessionManager, level: str, verbose: bool) -> bool:
    """Execute skm-wake sub-command using the existing mode_skm_wakeup logic."""
    terminal = sm.terminal

    if not isinstance(terminal, Elm327Terminal):
        messages.error("skm-wake needs an ELM327 transport (wican-ws / elm327-tcp).")
        return False

    success = await mode_skm_wakeup(terminal, level, verbose)
    if success:
        # Track the SKM session so keepalives are sent
        sm.track(SKM_TX_ID, SKM_SESSION_MODE)
    return success


async def exec_session(
    sm: SessionManager,
    target: str,
    wake: bool,
    ecu_index: dict[str, EcuIndexEntry],
    mode: int | str = DEFAULT_SESSION_MODE,
    *,
    profile: Profile,
) -> SessionOutcome | None:
    """Execute session sub-command; None when ``target`` names no ECU."""
    tx_id = resolve_tx_id(target, ecu_index, profile=profile)
    if tx_id is None:
        messages.error(f"unknown ECU '{target}'. Use a name (IGPM) or hex ID (770).")
        return None
    # A profile-declared wake ritual (canair.ecus.wake) for a fast-sleeping ECU is
    # honoured when --wake is set — the rapid-fire prime loop instead of a single
    # 10 01. Resolved into the ECU index by build_ecu_index; None = default wake.
    wake_plan = None
    if wake:
        key = ecu_index_key(target, ecu_index, profile=profile)
        entry = ecu_index.get(key) if key is not None else None
        if isinstance(entry, dict):
            wake_plan = entry.get("wake")
    if wake_plan is not None:
        print(f"  Waking 0x{tx_id:03X} ({target}) — {wake_plan.method} ritual…")
    shown = format_session_type(parse_session_type(mode))
    print(f"  Opening session (10{shown}) on 0x{tx_id:03X} ({target})…")
    return await sm.open_session(tx_id, wake=wake, mode=mode, wake_plan=wake_plan)


def build_query_plan(
    ecu_info: EcuIndexEntry,
    pid_filter: list[str],
    quiet: bool = False,
    include_static: bool = False,
):
    """Resolve an ECU + PID filter into a sorted query plan.

    Returns ``[(pid_code, pid_info_or_None, unmapped)]`` sorted by DID, or None
    if a non-empty ``pid_filter`` matched nothing. Filters pick definitions by
    :func:`canair.query.pids_named_by` (``BC03`` picks key ``22BC03``, ``01/0D``
    exactly ``010D``); an unmatched filter becomes the raw request
    :func:`canair.query.inferred_request` names (``01``->``2101``,
    ``B001``->``22B001``, ``22BC03`` verbatim, ``01/0D``->``010D``), or is warned
    about and skipped when it names no single read (``BC0301``, ``B01``).

    A bare ECU selector (empty ``pid_filter`` = "all PIDs") omits PIDs flagged
    ``status: static`` (identity/calibration blocks) or ``status: slow`` (per-cycle
    records such as 21F2 that change only when a drive or charge ends), unless
    ``include_static`` is set. Explicitly named PIDs are always queried,
    static or not — an explicit request wins.
    """
    pids_to_query = ecu_info["pids"]
    raw_pids: list[str] = []
    if pid_filter:
        picked: dict[str, PidIndexEntry] = {}
        for token in (p.upper() for p in pid_filter):
            hits = pids_named_by(token, pids_to_query)
            for key in hits:
                picked[key] = pids_to_query[key]
            if hits:
                continue
            try:
                request = inferred_request(token, ecu_info.get("memory_format"))
            except QueryError as e:
                if not quiet:
                    messages.warning(f"{e}; skipping")
                continue
            if request not in raw_pids:
                raw_pids.append(request)
        pids_to_query = picked
        if raw_pids and not quiet:
            messages.note(f"{', '.join(raw_pids)} not in ecus/ — querying raw")
        if not pids_to_query and not raw_pids:
            return None
    elif not include_static:
        # Bare ECU selector = all PIDs, minus static (identity/calibration) and slow (per-cycle
        # records like 21F2) ones: they change too rarely to be worth the bus time. `swept` is
        # derived from the PID's status in build_ecu_index.
        pids_to_query = {k: v for k, v in pids_to_query.items() if v.get("swept", True)}

    query_plan: list[tuple[str, PidIndexEntry | None, bool]] = [
        (pid_code, pid_info, False) for pid_code, pid_info in pids_to_query.items()
    ]
    query_plan += [(raw_pid, None, True) for raw_pid in raw_pids]
    query_plan.sort(key=lambda x: x[0])
    return query_plan


def _clamp_cap(sm: SessionManager, tx_id: int, cap: int, batch_state) -> int:
    """Clamp a requested batch size to the transport's single-request ceiling.

    Warned about once per (ECU, cap) so a mismatched profile is visible without
    spamming a poll loop; the warning names the transport because the same
    profile is correct on a segmenting one.
    """
    limit = transport_did_cap(sm.terminal.max_request_bytes)
    if cap <= limit:
        return cap
    if batch_state is not None and batch_state.note_clamp(tx_id, cap):
        log_event(
            "config",
            f"multi_did_max {cap} exceeds what {sm.terminal.diag.transport} can send "
            f"in one request; batching 0x{tx_id:03X} in groups of {limit}",
            level=logging.INFO,
            transport=sm.terminal.diag.transport,
            ecu=f"0x{tx_id:03X}",
        )
    return limit


async def _run_query_plan(
    sm: SessionManager, tx_id: int, query_plan, out, batch_state, max_dids=None, *, profile: Profile
):
    """Execute a query plan, batching consecutive service-22 DIDs when possible.

    Appends result dicts to ``out`` in plan order. With ``batch_state`` (and an
    ECU that opted into ``multi_did``), runs of consecutive 22-DIDs whose lengths
    are already known are read in one ``22 D1 D2 …`` request (up to ``max_dids``,
    defaulting to the batch state's cap); everything else is read singly. A batch
    that fails falls back to per-DID reads for that group, and a batch that keeps
    going unanswered while those fallbacks succeed stops batching for the ECU
    (:meth:`~canair.live.read.batch.BatchState.batch_unanswered`).

    The requested cap is clamped to what the *transport* can put on the wire
    (:func:`~canair.live.read.batch.transport_did_cap`). A profile may legally
    ask for more than an ELM327 can send, and discovering that from the adapter's
    bare ``?`` costs the ECU its batching for the whole session.
    """
    cap = max_dids if max_dids is not None else (batch_state.max_dids if batch_state else 1)
    cap = _clamp_cap(sm, tx_id, cap, batch_state)
    i, n = 0, len(query_plan)
    while i < n:
        code = query_plan[i][0]
        can_batch = (
            batch_state is not None
            and tx_id not in batch_state.disabled
            and _is_did22(code)
            and (tx_id, _did4(code)) in batch_state.lengths
        )
        if can_batch:
            group = []
            while (
                i < n
                and len(group) < cap
                and _is_did22(query_plan[i][0])
                and (tx_id, _did4(query_plan[i][0])) in batch_state.lengths
            ):
                group.append(query_plan[i])
                i += 1
            outcome = (
                await _read_batch(sm, tx_id, group, out, batch_state, profile=profile)
                if len(group) > 1
                else None
            )
            if outcome == "ok":
                continue
            # Single DID, or batch failed → per-DID. A failed batch's own time is charged
            # to none of its members: it ends in batching being given up (at once, or
            # after BATCH_UNANSWERED_BEFORE_DISABLE misses), and charging it would slow
            # healthy PIDs for a batch's fault.
            singles = [
                await _read_single(sm, tx_id, e[0], e[1], e[2], batch_state, profile=profile)
                for e in group
            ]
            out.extend(singles)
            if outcome == "unanswered" and any("raw_hex" in r for r in singles):
                batch_state.batch_unanswered(tx_id)  # the ECU is awake; its batches are not
            continue
        e = query_plan[i]
        out.append(await _read_single(sm, tx_id, e[0], e[1], e[2], batch_state, profile=profile))
        i += 1


async def exec_query(
    sm: SessionManager,
    ecu_name_str: str,
    pid_filter: list[str],
    ecu_index: dict[str, EcuIndexEntry],
    pids_data: dict,
    verbose: bool,
    return_results: bool = False,
    quiet: bool = False,
    batch_state: BatchState | None = None,
    include_static: bool = False,
    plan_filter: Callable[[str, list], list] | None = None,
    *,
    profile: Profile,
):
    """Execute query sub-command — query ECU parameters.

    Args:
        return_results: If True, return (ecu_label, pid_results) instead of printing.
        quiet: If True, suppress informational NOTE/WARNING prints (for monitor mode).
        batch_state: If provided and the ECU opts into ``multi_did``, batch UDS
            service-22 DIDs (learning per-DID lengths, auto-falling back per-DID
            on rejection). Used by the live monitor.
        plan_filter: Called with the canonical ECU name and the full query plan;
            returns the entries to actually read. The live monitor's per-PID
            scheduler uses it to read only the PIDs due this tick. An empty
            selection reads nothing and touches no device state.
    """
    ecu_key = ecu_index_key(ecu_name_str, ecu_index, profile=profile)
    if ecu_key is None:
        messages.error(f"unknown ECU '{ecu_name_str}'. Available: {', '.join(ecu_index.keys())}")
        return

    ecu_info = ecu_index[ecu_key]
    tx_id = ecu_info["tx_id"]
    ecu_label = f"{ecu_key} (0x{tx_id:03X})"

    query_plan = build_query_plan(ecu_info, pid_filter, quiet=quiet, include_static=include_static)
    if query_plan is None:
        messages.error(f"no matching PIDs for filter: {pid_filter}")
        messages.hint(f"available: {', '.join(sorted(ecu_info['pids']))}")
        return
    if plan_filter is not None:
        query_plan = plan_filter(ecu_key, query_plan)
        if not query_plan:
            return (ecu_label, []) if return_results else None

    # Refresh stale sessions before switching
    await sm.keepalive_stale()
    await sm.terminal.set_header(tx_id)

    all_pid_results = []

    batching = batch_state is not None and ecu_info.get("multi_did", False)
    await _run_query_plan(
        sm,
        tx_id,
        query_plan,
        all_pid_results,
        batch_state if batching else None,
        max_dids=ecu_info.get("multi_did_max"),
        profile=profile,
    )

    if return_results:
        return ecu_label, all_pid_results

    print_ecu_results(
        ecu_label=ecu_label,
        pid_results=all_pid_results,
        verbose=verbose,
    )


async def _exec_raw(sm: SessionManager, spec: str, hold: bool, verbose: bool, *, profile: Profile):
    """Execute raw sub-command."""
    try:
        tx_id, service_pid = parse_raw_spec(spec)
    except ValueError as e:
        messages.error(str(e))
        return

    await sm.keepalive_stale()
    print(f"\n  TX: 0x{tx_id:03X}  Request: {service_pid}")
    # One transaction, so a background keepalive (the REPL runs one) cannot re-point it.
    async with sm.terminal.transaction():
        await sm.terminal.set_header(tx_id)
        response = await sm.terminal.send_uds(service_pid)

    if not response["ok"]:
        error = response.get("error") or response.get("nrc_desc", "unknown error")
        if response.get("nrc") is not None:
            print(f"  NRC: 0x{response['nrc']:02X} -- {response['nrc_desc']}")
        else:
            messages.error(str(error))
    elif request_hidden(tx_id, service_pid, profile=profile):
        print(f"  Response: {MASK}  (sensitive — read --reveal to show)")
    else:
        decode = decode_uds_response(response["bytes"])
        if decode:
            print(f"  → {decode}")
            print(f"    Raw: {response['hex']}")
        else:
            print(f"  Response ({len(response['bytes'])} bytes): {response['hex']}")
            print()
            print_hexdump(response["bytes"])

    if hold:
        print("\n  Holding session (Ctrl+C to continue pipeline)…")
        sm.start_background_keepalive()
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("  Continuing…")
        finally:
            await sm.stop_background_keepalive()

    return tx_id, service_pid, response


async def _exec_iocontrol(
    sm: SessionManager,
    ecu_name: str,
    did: str,
    off: bool,
    pids_data: dict,
    ecu_index: dict,
    verbose: bool,
    *,
    profile: Profile,
):
    """Execute iocontrol sub-command within multi pipeline."""
    ioctrl_index = build_iocontrol_index(pids_data)
    ecu_key = ecu_index_key(ecu_name, ioctrl_index, profile=profile)
    did_key = did.upper()

    if ecu_key is None:
        messages.error(f"no IOControl DIDs for ECU: {ecu_name}")
        if ioctrl_index:
            messages.hint(f"ECUs with IOControl: {', '.join(sorted(ioctrl_index))}")
        return

    ecu_info = ioctrl_index[ecu_key]
    cmds = ecu_info["cmds"]

    if did_key not in cmds:
        available = sorted(cmds.keys())
        messages.error(f"unknown DID {did_key} for {ecu_key}")
        if available:
            messages.hint(f"available: {', '.join(available)}")
        return

    cmd_def = cmds[did_key]
    tx_id = ecu_info["tx_id"]
    action = "OFF" if off else "ON"
    hex_cmd = cmd_def["off"] if off else cmd_def["on"]
    label = cmd_def["label"]

    if not hex_cmd:
        messages.error(f"no {action} command defined for {ecu_key} {did_key} ({label})")
        return
    if not off:
        refusal = on_refusal(hex_cmd, cmd_def["release"], ecu_info["protocol"])
        if refusal:
            messages.error(f"{ecu_key} {did_key} ON refused — {refusal}")
            return

    # Ensure a session of the actuator's mode is open on this ECU if needed
    if cmd_def["session"]:
        mode = parse_session_type(cmd_def["session_mode"])
        if not sm.has_session(tx_id):
            await sm.open_session(tx_id, mode=mode)
        elif sm.session_mode(tx_id) != mode:
            await sm.open_session(tx_id, mode=mode, force=True)

    await sm.keepalive_stale()
    print(f"  {ecu_key} {did_key} ({label}) → {action}: {hex_cmd}")
    async with sm.terminal.transaction():
        await sm.terminal.set_header(tx_id)
        response = await send_iocontrol(sm.terminal, hex_cmd)

    if response["ok"]:
        print(f"  ✓ Positive response: {response['hex']}")
    elif response.get("nrc") is not None:
        print(f"  ✗ NRC 0x{response['nrc']:02X}: {response['nrc_desc']}")
    else:
        print(f"  ✗ Error: {response.get('error', 'unknown')}")


async def _exec_scan(
    sm: SessionManager,
    tx_str: str,
    service_str: str,
    range_str: str,
    append: str,
    verbose: bool,
    *,
    profile: Profile,
):
    """Execute scan sub-command."""

    tx_id = resolve_tx(tx_str, profile=profile)
    if tx_id is None:
        messages.error(f"could not resolve ECU {tx_str!r}")
        return
    try:
        service, _ = resolve_service(service_str)
    except ServiceError as e:
        messages.error(str(e))
        return

    try:
        pid_ranges = parse_id_ranges(range_str)
    except ValueError as e:
        messages.error(f"invalid range: {range_str} ({e})")
        return

    # A pipeline has no --yes, so the error names the command that takes it.
    flag = "--yes to `canair scan range` instead"
    if not confirm_actuating_scan(
        sm.terminal, tx_id, service, pid_ranges, append.upper(), flag=flag, profile=profile
    ):
        return

    await sm.keepalive_stale()

    # mode_scan handles its own header setting and session
    await mode_scan(
        sm.terminal,
        tx_id,
        service,
        pid_ranges,
        verbose,
        as_json=False,
        append_bytes=append.upper(),
        session=False,
        wake=False,
        profile=profile,
    )


def _rx_addr_for_tx(tx_id: int, *, profile: Profile) -> str:
    """Return the ECU CAN response address string for a TX id (e.g. "0x7EC")."""

    return rx_addr_str(tx_id, profile=profile)


def _rx_addr_for_ecu_label(ecu_label: str, ecu_index: dict, *, profile: Profile) -> str:
    """Resolve an ECU label (e.g. "BMS" or "BMS (0x7E4)") to its RX address.

    Falls back to the leading token verbatim if the ECU is not in the index.
    """

    # An ECU label always starts with a word char (e.g. "BMS" / "BMS (0x7E4)").
    m = re.match(r"(\w+)", ecu_label)
    assert m is not None
    ecu_short = m.group(1)
    info = ecu_index.get(ecu_short.upper())
    if info and info.get("tx_id") is not None:
        return rx_addr_str(info["tx_id"], profile=profile)
    return ecu_short
