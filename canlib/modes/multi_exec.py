"""Multi-ECU pipeline — per-step execution primitives.

The device-talking building blocks a multi pipeline (and the live monitor and
REPL) compose: opening sessions, reading a single PID or a batched multi-DID
group, resolving a query plan, and the ``query``/``raw``/``iocontrol``/``scan``
step executors. Orchestration (looping over steps, journaling) lives in
:mod:`canlib.modes.multi`; parsing in :mod:`canlib.modes.multi_parse`.
"""

import asyncio
import re
import time

from ..formatting import (
    decode_uds_response,
    print_ecu_results,
    print_hexdump,
)
from ..pids import EcuIndexEntry, PidIndexEntry, build_iocontrol_index
from ..session_manager import SessionManager
from ..terminal import WiCANTerminal
from ..uds_parse import request_echo
from .multi_batch import (
    BatchState,
    _decode_pid_result,
    _error_result,
    _is_did22,
    split_multi_did,
)
from .multi_parse import resolve_tx_id


async def _exec_skm_wake(sm: SessionManager, level: str, verbose: bool):
    """Execute skm-wake sub-command using the existing mode_skm_wakeup logic."""
    from .skm_wakeup import mode_skm_wakeup

    terminal = sm.terminal

    if not isinstance(terminal, WiCANTerminal):
        print("  skm-wake is only supported on the wican-ws (ELM327) transport.")
        return False

    success = await mode_skm_wakeup(terminal, level, verbose)
    if success:
        # Track the SKM session so keepalives are sent
        sm._sessions[0x7A5] = __import__("time").monotonic()
    return success


async def _exec_session(
    sm: SessionManager, target: str, wake: bool, ecu_index: dict, mode: str = "03"
):
    """Execute session sub-command."""
    tx_id = resolve_tx_id(target, ecu_index)
    if tx_id is None:
        print(f"  ERROR: Unknown ECU '{target}'. Use a name (IGPM) or hex ID (770).")
        return False
    # A profile-declared wake ritual (canlib.wake) for a fast-sleeping ECU is
    # honoured when --wake is set — the rapid-fire prime loop instead of a single
    # 10 01. Resolved into the ECU index by build_ecu_index; None = default wake.
    wake_plan = None
    if wake:
        entry = ecu_index.get(target.upper()) if isinstance(ecu_index, dict) else None
        if entry is None:
            from ..ecus import canonical_ecu_name_safe

            entry = ecu_index.get(canonical_ecu_name_safe(target).upper())
        if isinstance(entry, dict):
            wake_plan = entry.get("wake")
    if wake_plan is not None:
        print(f"  Waking 0x{tx_id:03X} ({target}) — {wake_plan.method} ritual...")
    print(f"  Opening session (10{mode.upper().zfill(2)}) on 0x{tx_id:03X} ({target})...")
    return await sm.open_session(tx_id, wake=wake, mode=mode, wake_plan=wake_plan)


async def _read_single(sm, tx_id, pid_code, pid_info, unmapped, batch_state):
    """Send one PID, return its result dict; learn its 22-DID length for batching.

    keepalive_stale + set_header are cheap under header caching (no-op / cache
    hit) yet re-establish the header if a background keepalive switched it.
    """
    await sm.keepalive_stale()
    await sm.terminal.set_header(tx_id)
    echo = request_echo(pid_code)
    expected_sid = echo[0] if echo else None
    expected_echo = echo[1] if echo else None
    resp = await sm.terminal.send_uds(
        pid_code, retries=1, expected_sid=expected_sid, expected_echo=expected_echo
    )
    # Timestamp the moment the response arrived so sequentially-polled PIDs keep
    # their true sub-second acquisition skew.
    acquired_at = time.time()
    if resp.get("ok") or resp.get("nrc") is not None:
        sm.mark_active(tx_id)  # a real answer resets S3 — no redundant 3E00 needed
    if not resp.get("ok"):
        return _error_result(pid_code, unmapped, resp, acquired_at)
    if batch_state is not None and _is_did22(pid_code) and resp.get("hex"):
        batch_state.learn(tx_id, pid_code[2:], resp["hex"])
    return _decode_pid_result(pid_code, pid_info, unmapped, resp["hex"], resp["bytes"], acquired_at)


async def _read_batch(sm, tx_id, group, out, batch_state) -> bool:
    """Attempt one multi-DID request for ``group``; append results on success.

    Returns True if the batch succeeded and split cleanly. On NRC 0x13/0x31
    (format/range not supported) or an unsplittable response, permanently
    disables batching for the ECU and returns False so the caller falls back to
    per-DID reads. Transient failures (e.g. NO DATA) return False without
    disabling.
    """
    dids = [e[0][2:] for e in group]
    await sm.keepalive_stale()
    await sm.terminal.set_header(tx_id)
    resp = await sm.terminal.send_uds("22" + "".join(dids))
    acquired_at = time.time()
    if resp.get("ok") or resp.get("nrc") is not None:
        sm.mark_active(tx_id)  # a real answer resets S3 — no redundant 3E00 needed
    if not resp.get("ok"):
        if resp.get("nrc") in (0x13, 0x31):
            batch_state.disabled.add(tx_id)
        return False
    split = split_multi_did(
        resp.get("hex", ""),
        [(d, batch_state.lengths[(tx_id, d)]) for d in dids],
        batch_state.pad,
    )
    if split is None:
        batch_state.disabled.add(tx_id)
        return False
    for pid_code, pid_info, unmapped in group:
        sub_hex = split[pid_code[2:]]
        out.append(
            _decode_pid_result(
                pid_code, pid_info, unmapped, sub_hex, bytes.fromhex(sub_hex), acquired_at
            )
        )
    return True


def build_query_plan(
    ecu_info: EcuIndexEntry,
    pid_filter: list[str],
    quiet: bool = False,
    include_static: bool = False,
):
    """Resolve an ECU + PID filter into a sorted query plan.

    Returns ``[(pid_code, pid_info_or_None, unmapped)]`` sorted by DID, or None
    if a non-empty ``pid_filter`` matched nothing. Filters match flexibly
    (``BC03`` matches key ``22BC03``); unmatched hex filters become raw UDS
    requests (``01``->``2101``, ``B001``->``22B001``, ``22BC03`` verbatim).

    A bare ECU selector (empty ``pid_filter`` = "all PIDs") omits PIDs flagged
    ``static: true`` (calibration/identity blocks like 21F2 that never change),
    unless ``include_static`` is set. Explicitly named PIDs are always queried,
    static or not — an explicit request wins.
    """
    pids_to_query = ecu_info["pids"]
    raw_pids: list[str] = []
    if pid_filter:
        filter_upper = [p.upper() for p in pid_filter]
        pids_to_query = {
            k: v
            for k, v in pids_to_query.items()
            if k.upper() in filter_upper or any(k.upper().endswith(f) for f in filter_upper)
        }
        matched_filters = set()
        for f in filter_upper:
            for k in pids_to_query:
                if k.upper() == f or k.upper().endswith(f):
                    matched_filters.add(f)
                    break
        for u in (f for f in filter_upper if f not in matched_filters):
            if all(c in "0123456789ABCDEF" for c in u):
                if len(u) <= 2:
                    raw_pids.append(f"21{u}")  # short KWP local ID: 01 -> 2101
                elif len(u) == 4 and u[:2] in ("21", "22"):
                    raw_pids.append(u)  # already service+id
                elif len(u) == 4:
                    raw_pids.append(f"22{u}")  # 4-char DID -> 22xxxx
                elif len(u) >= 5 and u[:2] in ("21", "22"):
                    raw_pids.append(u)  # full request code
                else:
                    raw_pids.append(f"22{u}")
            elif not quiet:
                print(f"  WARNING: Invalid PID format '{u}', skipping")
        if raw_pids and not quiet:
            print(f"  NOTE: {', '.join(raw_pids)} not in ecus/ — querying raw")
        if not pids_to_query and not raw_pids:
            return None
    elif not include_static:
        # Bare ECU selector = all PIDs. Skip static config/identity PIDs (21F2 &c.)
        # — they never change, so polling them in a sweep is wasted bus time.
        # `swept` is derived from the PID's status in build_ecu_index.
        pids_to_query = {k: v for k, v in pids_to_query.items() if v.get("swept", True)}

    query_plan: list[tuple[str, PidIndexEntry | None, bool]] = [
        (pid_code, pid_info, False) for pid_code, pid_info in pids_to_query.items()
    ]
    query_plan += [(raw_pid, None, True) for raw_pid in raw_pids]
    query_plan.sort(key=lambda x: x[0])
    return query_plan


async def _run_query_plan(sm, tx_id, query_plan, out, batch_state, max_dids=None):
    """Execute a query plan, batching consecutive service-22 DIDs when possible.

    Appends result dicts to ``out`` in plan order. With ``batch_state`` (and an
    ECU that opted into ``multi_did``), runs of consecutive 22-DIDs whose lengths
    are already known are read in one ``22 D1 D2 …`` request (up to ``max_dids``,
    defaulting to the batch state's cap); everything else is read singly. A batch
    that fails falls back to per-DID reads for that group.
    """
    cap = max_dids if max_dids is not None else (batch_state.max_dids if batch_state else 1)
    i, n = 0, len(query_plan)
    while i < n:
        code = query_plan[i][0]
        can_batch = (
            batch_state is not None
            and tx_id not in batch_state.disabled
            and _is_did22(code)
            and (tx_id, code[2:]) in batch_state.lengths
        )
        if can_batch:
            group = []
            while (
                i < n
                and len(group) < cap
                and _is_did22(query_plan[i][0])
                and (tx_id, query_plan[i][0][2:]) in batch_state.lengths
            ):
                group.append(query_plan[i])
                i += 1
            if len(group) > 1 and await _read_batch(sm, tx_id, group, out, batch_state):
                continue
            # Single DID, or batch failed → per-DID.
            for e in group:
                out.append(await _read_single(sm, tx_id, e[0], e[1], e[2], batch_state))
            continue
        e = query_plan[i]
        out.append(await _read_single(sm, tx_id, e[0], e[1], e[2], batch_state))
        i += 1


async def _exec_query(
    sm: SessionManager,
    ecu_name_str: str,
    pid_filter: list[str],
    ecu_index: dict,
    pids_data: dict,
    verbose: bool,
    return_results: bool = False,
    quiet: bool = False,
    batch_state: BatchState | None = None,
    include_static: bool = False,
):
    """Execute query sub-command — query ECU parameters.

    Args:
        return_results: If True, return (ecu_label, pid_results) instead of printing.
        quiet: If True, suppress informational NOTE/WARNING prints (for monitor mode).
        batch_state: If provided and the ECU opts into ``multi_did``, batch UDS
            service-22 DIDs (learning per-DID lengths, auto-falling back per-DID
            on rejection). Used by the live monitor.
    """
    upper = ecu_name_str.upper()
    if upper not in ecu_index:
        from ..ecus import canonical_ecu_name_safe

        upper = canonical_ecu_name_safe(ecu_name_str).upper()
    if upper not in ecu_index:
        print(f"  ERROR: Unknown ECU '{ecu_name_str}'. Available: {', '.join(ecu_index.keys())}")
        return

    ecu_info = ecu_index[upper]
    tx_id = ecu_info["tx_id"]

    # Refresh stale sessions before switching
    await sm.keepalive_stale()
    await sm.terminal.set_header(tx_id)

    query_plan = build_query_plan(ecu_info, pid_filter, quiet=quiet, include_static=include_static)
    if query_plan is None:
        print(f"  No matching PIDs for filter: {pid_filter}")
        print(f"  Available: {', '.join(sorted(ecu_info['pids'].keys()))}")
        return

    all_pid_results = []

    batching = batch_state is not None and ecu_info.get("multi_did", False)
    await _run_query_plan(
        sm,
        tx_id,
        query_plan,
        all_pid_results,
        batch_state if batching else None,
        max_dids=ecu_info.get("multi_did_max"),
    )

    ecu_label = f"{upper} (0x{tx_id:03X})"
    if return_results:
        return ecu_label, all_pid_results

    print_ecu_results(
        ecu_label=ecu_label,
        pid_results=all_pid_results,
        verbose=verbose,
    )


async def _exec_raw(sm: SessionManager, spec: str, hold: bool, verbose: bool):
    """Execute raw sub-command."""
    match = re.match(r"^([0-9A-Fa-f]{3})[:\s]([0-9A-Fa-f]+)$", spec)
    if not match:
        print(f"  ERROR: Invalid raw format: {spec}. Expected: TX:PID (e.g., 770:22BC03)")
        return

    tx_id = int(match.group(1), 16)
    service_pid = match.group(2).upper()

    await sm.keepalive_stale()
    await sm.terminal.set_header(tx_id)

    print(f"\n  TX: 0x{tx_id:03X}  Request: {service_pid}")
    response = await sm.terminal.send_uds(service_pid)

    if not response["ok"]:
        error = response.get("error") or response.get("nrc_desc", "unknown error")
        if response.get("nrc") is not None:
            print(f"  NRC: 0x{response['nrc']:02X} -- {response['nrc_desc']}")
        else:
            print(f"  Error: {error}")
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
        print("\n  Holding session (Ctrl+C to continue pipeline)...")
        sm.start_background_keepalive()
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("  Continuing...")
        finally:
            sm.stop_background_keepalive()

    return tx_id, service_pid, response


async def _exec_iocontrol(
    sm: SessionManager,
    ecu_name: str,
    did: str,
    off: bool,
    pids_data: dict,
    ecu_index: dict,
    verbose: bool,
):
    """Execute iocontrol sub-command within multi pipeline."""
    ioctrl_index = build_iocontrol_index(pids_data)
    ecu_key = ecu_name.upper()
    did_key = did.upper()

    if ecu_key not in ioctrl_index:
        available = sorted(ioctrl_index.keys())
        print(f"  ERROR: No IOControl DIDs for ECU: {ecu_name}")
        if available:
            print(f"  ECUs with IOControl: {', '.join(available)}")
        return

    ecu_info = ioctrl_index[ecu_key]
    cmds = ecu_info["cmds"]

    if did_key not in cmds:
        available = sorted(cmds.keys())
        print(f"  ERROR: Unknown DID {did_key} for {ecu_key}")
        if available:
            print(f"  Available: {', '.join(available)}")
        return

    cmd_def = cmds[did_key]
    tx_id = ecu_info["tx_id"]
    action = "OFF" if off else "ON"
    hex_cmd = cmd_def["off"] if off else cmd_def["on"]
    label = cmd_def["label"]

    if not hex_cmd:
        print(f"  ERROR: No {action} command defined for {ecu_key} {did_key} ({label})")
        return

    # Ensure session is active on this ECU if needed
    if cmd_def["session"] and tx_id not in sm.active_sessions:
        await sm.open_session(tx_id)

    await sm.keepalive_stale()
    await sm.terminal.set_header(tx_id)

    print(f"  {ecu_key} {did_key} ({label}) → {action}: {hex_cmd}")
    response = await sm.terminal.send_uds(hex_cmd, timeout=3.0)

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
):
    """Execute scan sub-command."""
    from ..ecus import resolve_tx
    from ..scan_presets import ServiceError, resolve_service
    from .scan import mode_scan

    tx_id = resolve_tx(tx_str)
    if tx_id is None:
        print(f"  ERROR: could not resolve ECU {tx_str!r}")
        return
    try:
        service, _ = resolve_service(service_str)
    except ServiceError as e:
        print(f"  ERROR: {e}")
        return
    match = re.match(r"^([0-9A-Fa-f]+)-([0-9A-Fa-f]+)$", range_str)
    if not match:
        print(f"  ERROR: Invalid range: {range_str}")
        return
    pid_range = (int(match.group(1), 16), int(match.group(2), 16))

    await sm.keepalive_stale()

    # mode_scan handles its own header setting and session
    await mode_scan(
        sm.terminal,
        tx_id,
        service,
        pid_range,
        verbose,
        as_json=False,
        append_bytes=append.upper(),
        session=False,
        wake=False,
    )


def _rx_addr_for_tx(tx_id: int) -> str:
    """Return the ECU CAN response address string for a TX id (e.g. "0x7EC")."""
    from ..ecus import rx_addr_str

    return rx_addr_str(tx_id)


def _rx_addr_for_ecu_label(ecu_label: str, ecu_index: dict) -> str:
    """Resolve an ECU label (e.g. "BMS" or "BMS (0x7E4)") to its RX address.

    Falls back to the leading token verbatim if the ECU is not in the index.
    """
    from ..ecus import rx_addr_str

    # An ECU label always starts with a word char (e.g. "BMS" / "BMS (0x7E4)").
    m = re.match(r"(\w+)", ecu_label)
    assert m is not None
    ecu_short = m.group(1)
    info = ecu_index.get(ecu_short.upper())
    if info and info.get("tx_id") is not None:
        return rx_addr_str(info["tx_id"])
    return ecu_short
