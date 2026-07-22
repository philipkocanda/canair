"""Multi-ECU pipeline mode.

Executes a sequence of sub-commands within a single transport session,
managing extended diagnostic sessions across multiple ECUs with interleaved
TesterPresent keepalives.

Sub-commands:
    skm-wake [level]                Wake SKM + activate relay (acc/ign1/ign2)
    session <ECU|TX_ID> [--wake]    Enter extended session on ECU
    query <QUERY>                   Query ECUs/PIDs via the mini-language
    raw <TX:PID>                    Raw UDS request
    scan <TX> <SVC> <RANGE> [APPEND]  Scan PID range
    sleep <seconds>                 Pause between steps
    repl                            Drop into interactive REPL (explicit)

The 'query' sub-command uses the ECU/PID selection mini-language (see
canlib.query): whitespace-separated ``ECU[:PID,PID,...]`` selectors. A bare
ECU queries all its PIDs; a cross-ECU query fans out to one query per ECU.

    query BMS                       all BMS PIDs
    query IGPM:BC03,BC06            two IGPM DIDs
    query VCU:2101 BMS:2101         cross-ECU (two ECUs)

After all sub-commands complete, exits by default. Use --repl to drop into
an interactive REPL, or include an explicit 'repl' step in the pipeline.
"""

import asyncio
import re
import shlex
import time

from ..decoding import decode_param_rows
from ..formatting import (
    decode_uds_response,
    print_ecu_results,
    print_hexdump,
)
from ..pids import build_ecu_index, build_iocontrol_index, build_param_index
from ..session_manager import SessionManager
from ..terminal import WiCANTerminal
from ..uds_parse import parse_uds_response


class BatchState:
    """Per-session UDS service-22 multi-DID batching state.

    Multi-DID support is per-ECU (some Hyundai ECUs answer ``22 D1 D2`` with
    ``62 D1 <data> D2 <data>``; others reject it with NRC 0x13). We learn each
    DID's data length from its first single read, batch once all target DIDs
    have known lengths, and permanently disable batching for an ECU that ever
    rejects it (or whose response fails to split) for the rest of the session.
    """

    def __init__(self):
        self.lengths: dict[tuple[int, str], int] = {}  # (tx_id, DID4) -> data bytes
        self.disabled: set[int] = set()  # tx_ids that don't support batching

    def learn(self, tx_id: int, did4: str, resp_hex: str) -> None:
        """Record a DID's data length from a single-DID ``62 DID <data>`` response."""
        dlen = _did_data_len(resp_hex, did4)
        if dlen is not None:
            self.lengths[(tx_id, did4.upper())] = dlen


def _strip_trailing_padding(data: bytes, pad: int = 0xAA) -> bytes:
    """Drop trailing ISO-TP padding bytes (Hyundai pads with 0xAA)."""
    i = len(data)
    while i > 0 and data[i - 1] == pad:
        i -= 1
    return data[:i]


def _did_data_len(resp_hex: str, did4: str) -> int | None:
    """Length (bytes) of a single-DID response's data, padding stripped.

    ``resp_hex`` is a ``62 <DID> <data> [AA…]`` positive response. Returns the
    number of data bytes after the 2-byte DID, or None if it doesn't parse.
    """
    try:
        b = bytes.fromhex(resp_hex)
        did = bytes.fromhex(did4)
    except ValueError:
        return None
    if len(b) < 3 or b[0] != 0x62 or b[1:3] != did:
        return None
    return len(_strip_trailing_padding(b[3:]))


def split_multi_did(resp_hex: str, dids_lengths: list[tuple[str, int]]) -> dict[str, str] | None:
    """Split a ``62`` multi-DID response into per-DID single-style responses.

    Args:
        resp_hex: reassembled UDS payload, ``62 D1 <data1> D2 <data2> … [AA…]``.
        dids_lengths: ordered ``(DID4, data_len_bytes)`` as requested.

    Returns ``{DID4: "62"+DID+data hex}`` (each looking like a normal single-DID
    response so existing decoders work unchanged), or ``None`` if the response
    doesn't match the expected DIDs/lengths (→ caller falls back to per-DID).
    """
    try:
        b = bytes.fromhex(resp_hex)
    except ValueError:
        return None
    if not b or b[0] != 0x62:
        return None
    pos = 1
    out: dict[str, str] = {}
    for did4, dlen in dids_lengths:
        try:
            did = bytes.fromhex(did4)
        except ValueError:
            return None
        if b[pos : pos + 2] != did or len(did) != 2:
            return None
        pos += 2
        data = b[pos : pos + dlen]
        if len(data) != dlen:
            return None
        pos += dlen
        out[did4.upper()] = (b"\x62" + did + data).hex().upper()
    # Anything left over must be padding only.
    if any(x != 0xAA for x in b[pos:]):
        return None
    return out


def resolve_tx_id(name_or_hex: str, ecu_index: dict) -> int | None:
    """Resolve an ECU name or hex TX ID to an integer.

    Accepts: 'IGPM', 'igpm', '770', '0x770', '7A0', and ecus.yaml aliases
    ('LDC' -> OBC, 'ABS' -> ESC).
    """
    from ..ecus import canonical_ecu_name_safe

    upper = canonical_ecu_name_safe(name_or_hex).upper()
    if upper in ecu_index:
        return ecu_index[upper]["tx_id"]

    # Try as hex
    cleaned = upper.removeprefix("0X")
    try:
        return int(cleaned, 16)
    except ValueError:
        return None


_HEX_DIGITS = frozenset("0123456789ABCDEF")


def _looks_like_pid(token: str) -> bool:
    """True if ``token`` looks like a bare PID/DID rather than an ECU name.

    Real ECU names are alphabetic (IGPM, BMS, VCU, …); PIDs/DIDs are hex tokens
    that contain a digit (2101, 22BC07, BC03, C00B, B00E). A bare hex-with-digit
    token in the ``ECU`` position is almost always a PID accidentally separated
    from its ECU by a space instead of a colon.
    """
    t = token.upper()
    return len(t) >= 2 and all(c in _HEX_DIGITS for c in t) and any(c.isdigit() for c in t)


def _query_selectors(tokens: list[str]) -> list[tuple[str, list[str]]]:
    """Expand ``query`` sub-command tokens into ``(ecu, pids)`` pairs.

    Tokens are parsed with the ECU/PID mini-language (canlib.query): each
    whitespace-separated ``ECU[:PID,PID,...]`` selector becomes one pair (a bare
    ECU yields an empty PID list = all PIDs). Identical selectors are de-duped so
    a repeated ECU/PID isn't polled twice. Raises ``QueryError`` (a ``ValueError``)
    on malformed input.

    Fails loudly on the classic space-vs-colon mistake: a bare selector that
    looks like a PID/DID (e.g. ``query IGPM 22BC07``, meant to be
    ``query IGPM:22BC07``) is rejected rather than silently treated as a query
    for a non-existent ECU named ``22BC07``.
    """
    from ..query import parse_query

    query = parse_query(tokens)
    prev_ecu: str | None = None
    for sel in query.selectors:
        if not sel.pids and _looks_like_pid(sel.ecu):
            if prev_ecu is not None:
                hint = (
                    f"Did you mean '{prev_ecu}:{sel.ecu}'? Attach the PID to its "
                    f"ECU with a colon (no space)."
                )
            else:
                hint = f"Attach it to an ECU with a colon, e.g. 'IGPM:{sel.ecu}'."
            raise ValueError(
                f"query selector {sel.ecu!r} looks like a PID/DID, not an ECU. {hint} "
                f"A space separates independent ECU selectors, so "
                f"'{prev_ecu or 'ECU'} {sel.ecu}' would query "
                f"{'ECU ' + repr(prev_ecu) + ' plus ' if prev_ecu else ''}"
                f"a non-existent ECU {sel.ecu!r}."
            )
        prev_ecu = sel.ecu

    pairs: list[tuple[str, list[str]]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for sel in query.selectors:
        key = (sel.ecu, sel.pids)
        if key in seen:
            continue
        seen.add(key)
        pairs.append((sel.ecu, list(sel.pids)))
    return pairs


def parse_sub_commands(args: list[str]) -> list[dict]:
    """Parse multi-mode sub-command strings into structured dicts.

    Each string is a mini-command like 'skm-wake acc' or 'raw 770:22BC03'.
    """
    commands = []
    for arg in args:
        parts = shlex.split(arg)
        if not parts:
            continue

        verb = parts[0].lower().replace("_", "-")

        if verb == "skm-wake":
            level = parts[1] if len(parts) > 1 else "acc"
            commands.append({"type": "skm-wake", "level": level})

        elif verb == "session":
            if len(parts) < 2:
                raise ValueError("'session' requires an ECU name or TX ID: session IGPM")
            wake = "--wake" in parts
            # Optional session mode: --mode XX (default 03 = UDS extended). Use 81
            # for KWP2000 standardDiagnosticSession on ECUs that reject 10 03.
            mode = "03"
            if "--mode" in parts:
                i = parts.index("--mode")
                if i + 1 >= len(parts):
                    raise ValueError("'session --mode' requires a hex value: session BMS --mode 81")
                mode = parts[i + 1]
            target = parts[1]
            commands.append({"type": "session", "target": target, "wake": wake, "mode": mode})

        elif verb == "query":
            if len(parts) < 2:
                raise ValueError(
                    "'query' requires a selection: query IGPM:BC03,BC06  or  query VCU:2101 BMS:2101"
                )
            for ecu, pids in _query_selectors(parts[1:]):
                commands.append({"type": "query", "ecu": ecu, "pids": pids})

        elif verb == "raw":
            if len(parts) < 2:
                raise ValueError("'raw' requires TX:PID: raw 770:22BC03")
            commands.append({"type": "raw", "spec": parts[1], "hold": "--hold" in parts})

        elif verb == "scan":
            # scan <TX> <SVC> <RANGE> [APPEND]
            if len(parts) < 4:
                raise ValueError("'scan' requires: scan <TX> <SERVICE> <RANGE> [APPEND]")
            commands.append(
                {
                    "type": "scan",
                    "tx": parts[1],
                    "service": parts[2],
                    "range": parts[3],
                    "append": parts[4] if len(parts) > 4 else "",
                }
            )

        elif verb == "sleep":
            seconds = float(parts[1]) if len(parts) > 1 else 1.0
            commands.append({"type": "sleep", "seconds": seconds})

        elif verb == "security":
            # security <ECU|TX_ID> [algo1 algo2 ...]
            if len(parts) < 2:
                raise ValueError("'security' requires an ECU name or TX ID: security BCM")
            target = parts[1]
            algos = parts[2:] if len(parts) > 2 else []
            commands.append({"type": "security", "target": target, "algos": algos})

        elif verb == "repl":
            commands.append({"type": "repl"})

        elif verb == "iocontrol":
            if len(parts) < 3:
                raise ValueError("'iocontrol' requires ECU and DID: iocontrol IGPM BC01 [--off]")
            ecu = parts[1]
            did = parts[2]
            off = "--off" in parts
            commands.append({"type": "iocontrol", "ecu": ecu, "did": did, "off": off})

        else:
            raise ValueError(
                f"Unknown sub-command: {verb!r}. "
                f"Available: skm-wake, session, query, raw, scan, sleep, "
                f"security, iocontrol, repl"
            )

    return commands


async def _exec_skm_wake(sm: SessionManager, level: str, verbose: bool):
    """Execute skm-wake sub-command using the existing mode_skm_wakeup logic."""
    from .skm_wakeup import mode_skm_wakeup

    terminal = sm.terminal

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
    print(f"  Opening session (10{mode.upper().zfill(2)}) on 0x{tx_id:03X} ({target})...")
    return await sm.open_session(tx_id, wake=wake, mode=mode)


def _is_did22(pid_code: str) -> bool:
    """True for a full 6-char service-22 DID request like ``22BC03``."""
    return len(pid_code) == 6 and pid_code[:2] == "22"


def _decode_pid_result(pid_code, pid_info, unmapped, hex_str, bytes_val, acquired_at):
    """Build a result dict from a successful (single or split-out) response."""
    if pid_info:
        return {
            "pid": pid_code,
            "params": decode_param_rows(hex_str, pid_info["parameters"]),
            "raw_hex": hex_str,
            "acquired_at": acquired_at,
        }
    return {
        "pid": pid_code,
        "params": [],
        "raw_hex": hex_str,
        "decode": decode_uds_response(bytes_val),
        "unmapped": True,
        "acquired_at": acquired_at,
    }


def _error_result(pid_code, unmapped, resp, acquired_at):
    error = resp.get("error") or resp.get("nrc_desc", "unknown")
    nrc = resp.get("nrc")
    if nrc is not None:
        error = f"NRC 0x{nrc:02X} ({resp['nrc_desc']})"
    return {"pid": pid_code, "error": error, "unmapped": unmapped, "acquired_at": acquired_at}


async def _read_single(sm, tx_id, pid_code, pid_info, unmapped, batch_state):
    """Send one PID, return its result dict; learn its 22-DID length for batching.

    keepalive_stale + set_header are cheap under header caching (no-op / cache
    hit) yet re-establish the header if a background keepalive switched it.
    """
    await sm.keepalive_stale()
    await sm.terminal.set_header(tx_id)
    resp = await sm.terminal.send_uds(pid_code)
    # Timestamp the moment the response arrived so sequentially-polled PIDs keep
    # their true sub-second acquisition skew.
    acquired_at = time.time()
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
    if not resp.get("ok"):
        if resp.get("nrc") in (0x13, 0x31):
            batch_state.disabled.add(tx_id)
        return False
    split = split_multi_did(
        resp.get("hex", ""), [(d, batch_state.lengths[(tx_id, d)]) for d in dids]
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


def build_query_plan(ecu_info: dict, pid_filter: list[str], quiet: bool = False):
    """Resolve an ECU + PID filter into a sorted query plan.

    Returns ``[(pid_code, pid_info_or_None, unmapped)]`` sorted by DID, or None
    if a non-empty ``pid_filter`` matched nothing. Filters match flexibly
    (``BC03`` matches key ``22BC03``); unmatched hex filters become raw UDS
    requests (``01``->``2101``, ``B001``->``22B001``, ``22BC03`` verbatim).
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
            print(f"  NOTE: {', '.join(raw_pids)} not in pids/ — querying raw")
        if not pids_to_query and not raw_pids:
            return None

    query_plan = [(pid_code, pid_info, False) for pid_code, pid_info in pids_to_query.items()]
    query_plan += [(raw_pid, None, True) for raw_pid in raw_pids]
    query_plan.sort(key=lambda x: x[0])
    return query_plan


async def _run_query_plan(sm, tx_id, query_plan, out, batch_state):
    """Execute a query plan, batching consecutive service-22 DIDs when possible.

    Appends result dicts to ``out`` in plan order. With ``batch_state`` (and an
    ECU that opted into ``multi_did``), runs of consecutive 22-DIDs whose lengths
    are already known are read in one ``22 D1 D2 …`` request (≤3 DIDs, so it
    stays a single-frame request); everything else is read singly. A batch that
    fails falls back to per-DID reads for that group.
    """
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
                and len(group) < 3
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

    query_plan = build_query_plan(ecu_info, pid_filter, quiet=quiet)
    if query_plan is None:
        print(f"  No matching PIDs for filter: {pid_filter}")
        print(f"  Available: {', '.join(sorted(ecu_info['pids'].keys()))}")
        return

    all_pid_results = []

    batching = batch_state is not None and ecu_info.get("multi_did", False)
    await _run_query_plan(sm, tx_id, query_plan, all_pid_results, batch_state if batching else None)

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
        await sm.ensure_session(tx_id)

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


# -- Helper functions for complex security algorithms --


def _popcount(n: int) -> int:
    """Count set bits in a 32-bit integer."""
    return bin(n & 0xFFFFFFFF).count("1")


def _rol32(val: int, n: int) -> int:
    """Rotate left 32-bit."""
    n &= 31
    return ((val << n) | (val >> (32 - n))) & 0xFFFFFFFF


def _ror32(val: int, n: int) -> int:
    """Rotate right 32-bit."""
    n &= 31
    return ((val >> n) | (val << (32 - n))) & 0xFFFFFFFF


def _ki203(seed: int, root: int) -> int:
    """KI203Algo: byte-swap seed → ROL3 → XOR root → ROR(popcount(root)) → byte-swap out."""
    # Byte-swap input: [B0,B1,B2,B3] → (B2 | B0<<8 | B3<<16 | B1<<24)
    b0, b1, b2, b3 = (
        (seed >> 24) & 0xFF,
        (seed >> 16) & 0xFF,
        (seed >> 8) & 0xFF,
        seed & 0xFF,
    )
    val = b2 | (b0 << 8) | (b3 << 16) | (b1 << 24)
    val = _rol32(val, 3)
    val ^= root
    val = _ror32(val, _popcount(root))
    # Byte-swap output: get bytes, reorder [B0,B2,B3,B1]
    o0, o1, o2, o3 = (
        (val >> 24) & 0xFF,
        (val >> 16) & 0xFF,
        (val >> 8) & 0xFF,
        val & 0xFF,
    )
    return (o0 << 24) | (o2 << 16) | (o3 << 8) | o1


def _ki221algo1(seed: int, root: int) -> int:
    """KI221Algo1: XOR root bytes with seed → byte-swap → (ROL29 = ROR3) → XOR root → ROL7."""
    # For 4-byte seed, XOR each root byte with corresponding seed byte
    rb = [(root >> 24) & 0xFF, (root >> 16) & 0xFF, (root >> 8) & 0xFF, root & 0xFF]
    sb = [(seed >> 24) & 0xFF, (seed >> 16) & 0xFF, (seed >> 8) & 0xFF, seed & 0xFF]
    k = [rb[0] ^ sb[0], rb[1] ^ sb[1], rb[2] ^ sb[2], rb[3] ^ sb[3]]
    # Byte-swap: [k0, k3, k2, k1] as little-endian → big-endian
    rs = k[0] | (k[3] << 8) | (k[2] << 16) | (k[1] << 24)
    inter = _ror32(rs, 3) ^ root
    return _rol32(inter, 7)


# -- Built-in security key algorithms --
# Each takes a 4-byte seed (int) and returns a 4-byte key (int).
SECURITY_ALGORITHMS = {
    "not": ("NOT (bitwise complement)", lambda s: (~s) & 0xFFFFFFFF),
    "xor-0d0b0507": ("XOR 0x0D0B0507", lambda s: s ^ 0x0D0B0507),
    "swap": (
        "byte-swap (reverse order)",
        lambda s: (
            ((s & 0xFF) << 24) | ((s & 0xFF00) << 8) | ((s & 0xFF0000) >> 8) | ((s >> 24) & 0xFF)
        ),
    ),
    "plus1": ("seed + 1", lambda s: (s + 1) & 0xFFFFFFFF),
    "minus1": ("seed - 1", lambda s: (s - 1) & 0xFFFFFFFF),
    "same": ("key = seed (echo)", lambda s: s),
    "xor-5a": ("XOR 0x5A5A5A5A", lambda s: s ^ 0x5A5A5A5A),
    "xor-a5": ("XOR 0xA5A5A5A5", lambda s: s ^ 0xA5A5A5A5),
    "xor-1234": ("XOR 0x12345678", lambda s: s ^ 0x12345678),
    "xor-dead": ("XOR 0xDEADBEEF", lambda s: s ^ 0xDEADBEEF),
    "xor-9876": ("XOR 0x98765432", lambda s: s ^ 0x98765432),
    "zero": ("key = 0x00000000", lambda s: 0x00000000),
    "xor-ffff": ("XOR 0xFFFFFFFF (= NOT)", lambda s: s ^ 0xFFFFFFFF),
    "mul3plus1": ("seed * 3 + 1", lambda s: (s * 3 + 1) & 0xFFFFFFFF),
    "swap16": (
        "swap 16-bit halves",
        lambda s: ((s & 0xFFFF) << 16) | ((s >> 16) & 0xFFFF),
    ),
    "ror8": ("rotate right 8 bits", lambda s: ((s >> 8) | (s << 24)) & 0xFFFFFFFF),
    "rol8": ("rotate left 8 bits", lambda s: ((s << 8) | (s >> 24)) & 0xFFFFFFFF),
    "static-6fd5": ("static key 0x6FD56FD5 (Kia Soul)", lambda s: 0x6FD56FD5),
    "xor-6fd5": ("XOR 0x6FD56FD5", lambda s: s ^ 0x6FD56FD5),
    "add-6fd5": ("ADD 0x6FD56FD5", lambda s: (s + 0x6FD56FD5) & 0xFFFFFFFF),
    "sub-6fd5": ("SUB 0x6FD56FD5", lambda s: (s - 0x6FD56FD5) & 0xFFFFFFFF),
    "not-plus1": ("NOT + 1 (two's complement neg)", lambda s: ((~s) + 1) & 0xFFFFFFFF),
    "ror4": ("rotate right 4 bits", lambda s: ((s >> 4) | (s << 28)) & 0xFFFFFFFF),
    "rol4": ("rotate left 4 bits", lambda s: ((s << 4) | (s >> 28)) & 0xFFFFFFFF),
    "ror16": ("rotate right 16 bits", lambda s: ((s >> 16) | (s << 16)) & 0xFFFFFFFF),
    "swap-not": (
        "byte-swap then NOT",
        lambda s: (
            (
                ~(
                    ((s & 0xFF) << 24)
                    | ((s & 0xFF00) << 8)
                    | ((s & 0xFF0000) >> 8)
                    | ((s >> 24) & 0xFF)
                )
            )
            & 0xFFFFFFFF
        ),
    ),
    "not-swap": (
        "NOT then byte-swap",
        lambda s: (
            (
                ((~s & 0xFF) << 24)
                | ((~s & 0xFF00) << 8)
                | ((~s & 0xFF0000) >> 8)
                | (((~s) >> 24) & 0xFF)
            )
            & 0xFFFFFFFF
        ),
    ),
    "xor-swap": (
        "XOR 0xAAAAAAAA then byte-swap",
        lambda s: (
            lambda x: (
                ((x & 0xFF) << 24)
                | ((x & 0xFF00) << 8)
                | ((x & 0xFF0000) >> 8)
                | ((x >> 24) & 0xFF)
            )
        )(s ^ 0xAAAAAAAA),
    ),
    "per-byte-not": (
        "NOT each byte independently",
        lambda s: s ^ 0xFFFFFFFF,
    ),  # same as not
    "add1-per-byte": (
        "add 1 to each byte",
        lambda s: (
            (((s >> 24) + 1) & 0xFF) << 24
            | ((((s >> 16) & 0xFF) + 1) & 0xFF) << 16
            | ((((s >> 8) & 0xFF) + 1) & 0xFF) << 8
            | (((s & 0xFF) + 1) & 0xFF)
        ),
    ),
    "sub1-per-byte": (
        "sub 1 from each byte",
        lambda s: (
            (((s >> 24) - 1) & 0xFF) << 24
            | ((((s >> 16) & 0xFF) - 1) & 0xFF) << 16
            | ((((s >> 8) & 0xFF) - 1) & 0xFF) << 8
            | (((s & 0xFF) - 1) & 0xFF)
        ),
    ),
    # --- KI221Algo2: key = (seed ^ XOR) + ADD (known constant pairs) ---
    "ki221-std": (
        "KI221Algo2 XOR 0x78253947 + ADD 0x83249272",
        lambda s: ((s ^ 0x78253947) + 0x83249272) & 0xFFFFFFFF,
    ),
    "ki221-std-rev": (
        "KI221Algo2 ADD first then XOR",
        lambda s: ((s + 0x83249272) ^ 0x78253947) & 0xFFFFFFFF,
    ),
    # --- KI203Algo: swap-seed → ROL3 → XOR root → ROR(popcount(root)) → swap-out ---
    **{
        f"ki203-{hex(root)[2:]}": (
            f"KI203Algo root=0x{root:08X}",
            (lambda r: lambda s: _ki203(s, r))(root),
        )
        for root in [
            0x30BACD45,
            0x27FC2D10,
            0x4902EF27,
            0xBADEF289,
            0x62FB90EF,
            0x3EFA72D6,
            0x3913B1FF,
            0x4532F3EF,
            0x2A58122F,
        ]
    },
    # --- KI221Algo1: XOR seed with root bytes → swap → ROL29 → XOR root → ROL7 ---
    **{
        f"ki221a1-{hex(root)[2:]}": (
            f"KI221Algo1 root=0x{root:08X}",
            (lambda r: lambda s: _ki221algo1(s, r))(root),
        )
        for root in [
            0x3913B1FF,
            0x4532F3EF,
            0x2A58122F,
            0x78253947,
            0x83249272,
            0x30BACD45,
        ]
    },
}


def solve_key_pair(seed: int, key: int, seed_len: int = 4) -> list[tuple[str, str]]:
    """Return the algorithms that reproduce ``key`` from ``seed`` (offline).

    Iterates :data:`SECURITY_ALGORITHMS`, masking each result to the seed's byte
    width, and returns ``(name, description)`` for every algorithm that maps
    ``seed`` -> ``key``. Feed it a seed/key pair sniffed from a working scan tool
    to identify (or confirm) the ECU's SecurityAccess algorithm without touching
    the car.
    """
    mask = (1 << (seed_len * 8)) - 1
    key &= mask
    matches: list[tuple[str, str]] = []
    for name, (desc, fn) in SECURITY_ALGORITHMS.items():
        try:
            if (fn(seed) & mask) == key:
                matches.append((name, desc))
        except Exception:
            continue
    return matches


async def _exec_security(
    sm: SessionManager,
    target: str,
    algo_filter: list[str],
    ecu_index: dict,
    verbose: bool,
) -> bool:
    """Try UDS Security Access (27 01/02) with common key algorithms.

    Requests a seed (27 01), computes a key using each algorithm, sends the key
    (27 02 <key>). Handles NRC 0x35 (invalidKey), 0x36 (exceededNumberOfAttempts),
    and 0x37 (requiredTimeDelayNotExpired).

    Args:
        sm: Session manager (session must already be open on the target ECU).
        target: ECU name or hex TX ID.
        algo_filter: If non-empty, only try these algorithm names. Otherwise try all.
        ecu_index: ECU name → info dict.
        verbose: Show debug output.

    Returns:
        True if security access was granted.
    """
    tx_id = resolve_tx_id(target, ecu_index)
    if tx_id is None:
        print(f"  ERROR: Unknown ECU '{target}'.")
        return False

    # Select algorithms
    if algo_filter:
        algos = []
        for name in algo_filter:
            key = name.lower().replace("_", "-")
            if key in SECURITY_ALGORITHMS:
                algos.append((key, *SECURITY_ALGORITHMS[key]))
            else:
                print(
                    f"  WARNING: Unknown algorithm '{name}'. Available: {', '.join(SECURITY_ALGORITHMS.keys())}"
                )
        if not algos:
            return False
    else:
        algos = [(k, desc, fn) for k, (desc, fn) in SECURITY_ALGORITHMS.items()]

    print(f"\n  Security Access on 0x{tx_id:03X} — trying {len(algos)} algorithm(s)")
    print(f"  {'Algorithm':<30} {'Seed':>10}  {'Key':>10}  Result")
    print(f"  {'─' * 30} {'─' * 10}  {'─' * 10}  {'─' * 20}")

    for algo_name, algo_desc, algo_fn in algos:
        # Ensure we're on the right ECU and session is fresh
        await sm.keepalive_stale()
        await sm.terminal.set_header(tx_id)

        # Request seed (with retry loop for lockout recovery)
        resp = None
        for _attempt in range(3):
            resp = await sm.terminal.send_uds("2701", timeout=5.0)

            if resp.get("ok"):
                break

            nrc = resp.get("nrc")
            if nrc == 0x37:  # requiredTimeDelayNotExpired
                print(
                    f"  {'(delay)':<30} {'—':>10}  {'—':>10}  Locked — waiting 11s + re-establishing session..."
                )
                await asyncio.sleep(11)
                # Re-establish extended session (BCM drops it after lockout)
                await sm.open_session(tx_id)
                continue
            elif nrc == 0x7F:  # serviceNotSupportedInActiveSession
                print(
                    f"  {'(session)':<30} {'—':>10}  {'—':>10}  Session dropped — re-establishing..."
                )
                await sm.open_session(tx_id)
                continue
            elif nrc == 0x36:  # exceededNumberOfAttempts
                print(f"  {algo_name:<30} {'—':>10}  {'—':>10}  LOCKOUT — stopping.")
                return False
            else:
                break  # Other error, let it fall through

        if not resp or not resp.get("ok"):
            nrc = resp.get("nrc") if resp else None
            desc = resp.get("nrc_desc") or resp.get("error", "unknown") if resp else "no response"
            print(f"  {algo_name:<30} {'—':>10}  {'—':>10}  Seed failed: {desc}")
            continue

        # Parse seed from response bytes (67 01 <seed...>). The seed length is
        # ECU-specific (commonly 4 bytes, but 2/3/6 occur), so read ALL bytes
        # after the 67 01 echo rather than assuming 4.
        raw_bytes = resp.get("bytes", b"")
        if len(raw_bytes) < 3 or raw_bytes[0] != 0x67 or raw_bytes[1] != 0x01:
            print(
                f"  {algo_name:<30} {'—':>10}  {'—':>10}  Bad seed response: {resp.get('hex', '?')}"
            )
            continue

        seed_bytes = raw_bytes[2:]
        seed_len = len(seed_bytes)
        seed = int.from_bytes(seed_bytes, "big")
        seed_hex = seed_bytes.hex().upper()

        if seed == 0:
            # An all-zero seed usually means the ECU is already unlocked (or the
            # level needs no key). Surface it and stop hammering.
            print(
                f"  {algo_name:<30} {seed_hex:>10}  {'—':>10}  seed is all-zero — "
                "likely already unlocked / no key required"
            )
            return True

        # Compute key, masked and formatted to the seed's byte width (the built-in
        # algorithms are 32-bit-oriented; for non-4-byte seeds this is best-effort
        # and clearly a guess — the raw seed above is what to feed --pair).
        mask = (1 << (seed_len * 8)) - 1
        key = algo_fn(seed) & mask
        key_hex = f"{key:0{seed_len * 2}X}"

        # Send key
        key_resp = await sm.terminal.send_uds(f"2702{key_hex}", timeout=5.0)

        if key_resp.get("ok"):
            raw_key_bytes = key_resp.get("bytes", b"")
            if len(raw_key_bytes) >= 2 and raw_key_bytes[0] == 0x67 and raw_key_bytes[1] == 0x02:
                print(f"  {algo_name:<30} {seed_hex:>10}  {key_hex:>10}  *** ACCEPTED ***")
                print(f"\n  Security access GRANTED on 0x{tx_id:03X}!")
                print(f"  Algorithm: {algo_desc}")
                print(f"  Seed: 0x{seed_hex}  Key: 0x{key_hex}")
                return True
            else:
                print(
                    f"  {algo_name:<30} {seed_hex:>10}  {key_hex:>10}  Unexpected OK: {key_resp.get('hex', '?')}"
                )
        else:
            nrc = key_resp.get("nrc")
            if nrc == 0x35:  # invalidKey
                print(f"  {algo_name:<30} {seed_hex:>10}  {key_hex:>10}  invalid key")
            elif nrc == 0x36:  # exceededNumberOfAttempts
                print(f"  {algo_name:<30} {seed_hex:>10}  {key_hex:>10}  LOCKOUT — stopping.")
                return False
            elif nrc == 0x37:  # requiredTimeDelayNotExpired
                print(
                    f"  {algo_name:<30} {seed_hex:>10}  {key_hex:>10}  delay penalty (will retry next)"
                )
            else:
                desc = key_resp.get("nrc_desc") or key_resp.get("error", "unknown")
                print(f"  {algo_name:<30} {seed_hex:>10}  {key_hex:>10}  {desc}")

    print("\n  No algorithm worked. Security access denied.")
    print(
        "  Tip: if you can sniff a working tool's seed→key exchange, identify the "
        "algorithm offline with:  canair query security --pair SEED:KEY"
    )
    return False


def _rx_addr_for_tx(tx_id: int) -> str:
    """Return the ECU CAN response address string for a TX id (e.g. "0x7EC")."""
    from ..ecus import rx_addr_str

    return rx_addr_str(tx_id)


def _rx_addr_for_ecu_label(ecu_label: str, ecu_index: dict) -> str:
    """Resolve an ECU label (e.g. "BMS" or "BMS (0x7E4)") to its RX address.

    Falls back to the leading token verbatim if the ECU is not in the index.
    """
    from ..ecus import rx_addr_str

    ecu_short = re.match(r"(\w+)", ecu_label).group(1)
    info = ecu_index.get(ecu_short.upper())
    if info and info.get("tx_id") is not None:
        return rx_addr_str(info["tx_id"])
    return ecu_short


def _finalize_journal(
    journal,
    count: int,
    label: str | None,
    state: str | None,
    notes: str | None,
    prompt: bool = True,
    suggested_state: str | None = None,
) -> None:
    """Resolve metadata (optionally prompting) and reconcile the write-ahead journal.

    ``journal`` is a :class:`~canlib.capture_journal.CaptureJournal` (or None).
    On a cancelled interactive prompt the journal is discarded. When ``prompt``
    is False (e.g. an interrupted pipeline) the journal is reconciled with the
    metadata already on it — no stdin interaction. ``suggested_state`` pre-fills
    the interactive state prompt (auto-suggested from decoded PID values).
    """
    from ..captures import resolve_metadata

    if journal is None:
        return
    if count == 0:
        print("\n  --save: no payloads captured — nothing to save.")
        journal.discard()
        return

    print(f"\n  --save: {count} payload(s) captured.")
    if prompt:
        meta = resolve_metadata(
            label, state, notes, suggested_label="Multi query session",
            last_state=suggested_state,
        )
        if meta is None:
            journal.discard()
            return
        lbl, st, nt = meta
        journal.update_meta(lbl, st, nt)
    journal.reconcile()


async def mode_multi(
    terminal: WiCANTerminal,
    sub_commands: list[str],
    pids_data: dict,
    verbose: bool,
    no_repl: bool = False,
    save: bool = False,
    label: str | None = None,
    state: str | None = None,
    notes: str | None = None,
):
    """Execute a multi-ECU pipeline and optionally drop into REPL.

    Args:
        terminal: Connected WiCANTerminal.
        sub_commands: List of sub-command strings (e.g., ["skm-wake acc", "query IGPM:BC03"]).
        pids_data: Loaded PID definitions.
        verbose: Show debug output.
        no_repl: If True, don't drop into REPL after pipeline.
        save: If True, collect payloads from query/raw steps and save them to
            captures/YYYY-MM-DD.yaml after the pipeline completes.
        label/state/notes: Session metadata. When ``label`` is provided, saving
            is non-interactive; otherwise the user is prompted.
    """
    commands = parse_sub_commands(sub_commands)
    ecu_index = build_ecu_index(pids_data)
    sm = SessionManager(terminal, verbose=verbose)
    repl_executed = False
    # Collected (ecu_ref, pid, hex, time) rows for --save (also counts for report).
    collected: list[tuple[str, str, str, str]] = []
    # Accumulated decoded values for state auto-suggestion at save time.
    pipe_values: dict[str, float] = {}
    pipe_responded: set[str] = set()
    # Write-ahead journal: payloads are appended as they arrive and reconciled at
    # the end. An exception mid-pipeline leaves it on disk for `--recover`.
    journal = None
    if save:
        from ..capture_journal import CaptureJournal
        from ..profile import active

        journal = CaptureJournal.open(
            active().captures_dir,
            label=label or "Multi query session",
            state=state,
            notes=notes,
            source="query",
        )

    def _collect_query(ecu_label: str, pid_results: list[dict]) -> None:
        ecu_ref = _rx_addr_for_ecu_label(ecu_label, ecu_index)
        for entry in pid_results or []:
            raw_hex = entry.get("raw_hex", "")
            if raw_hex:
                collected.append((ecu_ref, entry["pid"], raw_hex, ""))
                if journal is not None:
                    journal.append(ecu_ref, entry["pid"], raw_hex)
        # Accumulate decoded values for end-of-pipeline state auto-suggestion.
        if save:
            from ..states import collect_values

            vals, resp = collect_values([(ecu_label, pid_results)])
            pipe_values.update(vals)
            pipe_responded.update(resp)

    def _suggest_pipeline_state() -> str | None:
        from ..states import StatePredicateError, load_states, suggest_state

        try:
            rules = load_states()
        except StatePredicateError:
            return None
        if not rules:
            return None
        return suggest_state(rules, pipe_values, pipe_responded)

    try:
        for i, cmd in enumerate(commands):
            cmd_type = cmd["type"]
            step = f"[{i + 1}/{len(commands)}]"

            if cmd_type == "skm-wake":
                print(f"\n{step} SKM wakeup ({cmd['level']})...")
                await _exec_skm_wake(sm, cmd["level"], verbose)

            elif cmd_type == "session":
                print(f"\n{step} Session on {cmd['target']}...")
                await _exec_session(sm, cmd["target"], cmd["wake"], ecu_index, cmd.get("mode", "03"))

            elif cmd_type == "query":
                pids_str = " ".join(cmd["pids"]) if cmd["pids"] else "all"
                print(f"\n{step} Query {cmd['ecu']} ({pids_str})...")
                if save:
                    result = await _exec_query(
                        sm,
                        cmd["ecu"],
                        cmd["pids"],
                        ecu_index,
                        pids_data,
                        verbose,
                        return_results=True,
                    )
                    if result:
                        ecu_label, pid_results = result
                        print_ecu_results(
                            ecu_label=ecu_label, pid_results=pid_results, verbose=verbose
                        )
                        _collect_query(ecu_label, pid_results)
                else:
                    await _exec_query(sm, cmd["ecu"], cmd["pids"], ecu_index, pids_data, verbose)

            elif cmd_type == "raw":
                print(f"\n{step} Raw {cmd['spec']}...")
                raw_result = await _exec_raw(sm, cmd["spec"], cmd["hold"], verbose)
                if save and raw_result:
                    tx_id, req, resp = raw_result
                    if resp.get("ok") and resp.get("hex"):
                        ecu_ref = _rx_addr_for_tx(tx_id)
                        collected.append((ecu_ref, req, resp["hex"], ""))
                        if journal is not None:
                            journal.append(ecu_ref, req, resp["hex"])

            elif cmd_type == "scan":
                print(f"\n{step} Scan {cmd['tx']} service {cmd['service']} range {cmd['range']}...")
                await _exec_scan(
                    sm, cmd["tx"], cmd["service"], cmd["range"], cmd["append"], verbose
                )

            elif cmd_type == "sleep":
                print(f"\n{step} Sleeping {cmd['seconds']}s...")
                # Send keepalives during sleep to maintain active sessions
                remaining = cmd["seconds"]
                while remaining > 0:
                    chunk = min(remaining, 1.5)
                    await asyncio.sleep(chunk)
                    remaining -= chunk
                    if sm.active_sessions:
                        await sm.keepalive_stale()

            elif cmd_type == "security":
                algos_str = " ".join(cmd["algos"]) if cmd["algos"] else "all"
                print(f"\n{step} Security access on {cmd['target']} ({algos_str})...")
                await _exec_security(sm, cmd["target"], cmd["algos"], ecu_index, verbose)

            elif cmd_type == "repl":
                print(f"\n{step} Entering REPL...")
                repl_executed = True
                await _multi_repl(sm, ecu_index, pids_data, verbose)

            elif cmd_type == "iocontrol":
                action = "OFF" if cmd["off"] else "ON"
                print(f"\n{step} IOControl {cmd['ecu']} {cmd['did']} ({action})...")
                await _exec_iocontrol(
                    sm, cmd["ecu"], cmd["did"], cmd["off"], pids_data, ecu_index, verbose
                )

        # Save collected payloads before any REPL handoff
        if save:
            _finalize_journal(
                journal, len(collected), label, state, notes,
                suggested_state=_suggest_pipeline_state(),
            )
            journal = None

        # Auto-REPL if no explicit repl step and --repl was passed
        if not repl_executed and not no_repl:
            sessions_str = ", ".join(f"0x{tx:03X}" for tx in sm.active_sessions)
            if sessions_str:
                print(f"\n  Active sessions: {sessions_str}")
            print("\n  Pipeline complete. Entering REPL...")
            await _multi_repl(sm, ecu_index, pids_data, verbose)

    except KeyboardInterrupt:
        print("\n  Interrupted.")
        # Reconcile whatever was captured before the interrupt (no prompt).
        if save and journal is not None:
            _finalize_journal(journal, len(collected), label, state, notes, prompt=False)
            journal = None

    finally:
        sm.stop_background_keepalive()
        print("  Closing all sessions...")
        try:
            await asyncio.wait_for(sm.close_all(), timeout=3.0)
        except (TimeoutError, KeyboardInterrupt, Exception):
            pass


async def _multi_repl(sm: SessionManager, ecu_index: dict, pids_data: dict, verbose: bool):
    """Interactive REPL with multi-ECU session awareness.

    Extends the standard REPL with session keepalives and multi-ECU commands.
    """
    from .skm_wakeup import mode_skm_wakeup

    terminal = sm.terminal
    _param_index = build_param_index(pids_data)
    last_tx_id = None

    # Start background keepalive for all tracked sessions
    sm.start_background_keepalive(interval=2.0)

    print()
    print("Multi-ECU REPL -- sessions are kept alive automatically")
    sessions_str = ", ".join(f"0x{tx:03X}" for tx in sm.active_sessions)
    if sessions_str:
        print(f"  Active sessions: {sessions_str}")
    print()
    print("Commands:")
    print("  AT commands      ATZ, ATSH7E4, etc.")
    print("  UDS requests     2101, 22BC03, etc.")
    print("  session <ECU>    Open extended session on ECU")
    print("  sessions         List active sessions")
    print("  skm [level]      SKM wakeup")
    print("  query <ECU> [PID ...]  Query ECU parameters")
    print("  raw <TX:PID>     Raw UDS request")
    print("  security <ECU>   Try security access (27 01/02) with common algorithms")
    print("  quit / Ctrl+C    Exit REPL")
    print("  (! prefix optional: !query = query)")
    print()

    try:
        # Use asyncio stdin reader instead of run_in_executor(input()) —
        # input() blocks a thread pool thread that can't be interrupted by Ctrl+C
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), __import__("sys").stdin
        )

        import signal
        import sys

        # Set up SIGINT to cancel the current readline gracefully
        repl_quit = asyncio.Event()
        _old_handler = signal.getsignal(signal.SIGINT)

        def _sigint_handler(sig, frame):
            repl_quit.set()

        signal.signal(signal.SIGINT, _sigint_handler)

        while not repl_quit.is_set():
            sys.stdout.write("multi> ")
            sys.stdout.flush()

            # Race: readline vs quit signal
            read_task = asyncio.ensure_future(reader.readline())
            quit_task = asyncio.ensure_future(repl_quit.wait())
            done, pending = await asyncio.wait(
                [read_task, quit_task], return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()

            if repl_quit.is_set():
                print("\nExiting REPL...")
                break

            if read_task in done:
                line = read_task.result()
            else:
                break

            if not line:  # EOF
                print("\nExiting REPL...")
                break

            cmd = line.decode("utf-8", errors="replace").strip()
            if not cmd:
                continue

            # Strip optional ! prefix for built-in commands
            cmd_lower = cmd.lower().lstrip("!")

            if cmd_lower in ("quit", "exit", "q"):
                break

            if cmd_lower == "sessions":
                if sm.active_sessions:
                    for tx_id in sm.active_sessions:
                        name = "?"
                        for n, info in ecu_index.items():
                            if info["tx_id"] == tx_id:
                                name = n
                                break
                        print(f"  0x{tx_id:03X} ({name})")
                else:
                    print("  No active sessions.")
                continue

            if cmd_lower.startswith("session "):
                target = cmd.split()[1]
                tx_id = resolve_tx_id(target, ecu_index)
                if tx_id is None:
                    print(f"  Unknown ECU: {target}")
                else:
                    sm.stop_background_keepalive()
                    await sm.open_session(tx_id)
                    sm.start_background_keepalive(interval=2.0)
                    print(f"  Session opened on 0x{tx_id:03X}")
                continue

            if cmd_lower.startswith("skm"):
                parts = cmd.split()
                level = parts[1] if len(parts) > 1 else "acc"
                sm.stop_background_keepalive()
                await mode_skm_wakeup(terminal, level, verbose)
                sm._sessions[0x7A5] = __import__("time").monotonic()
                sm.start_background_keepalive(interval=2.0)
                continue

            if cmd_lower.startswith("query "):
                parts = cmd.split()
                # First token might be "query" or "!query"; rest is a mini-language query.
                try:
                    selectors = _query_selectors(parts[1:])
                except ValueError as ex:
                    print(f"  Invalid query: {ex}")
                    continue
                sm.stop_background_keepalive()
                for ecu, pids in selectors:
                    await _exec_query(sm, ecu, pids, ecu_index, pids_data, verbose)
                sm.start_background_keepalive(interval=2.0)
                continue

            if cmd_lower.startswith("raw "):
                spec = cmd.split(None, 1)[1]
                # Strip leading ! if present
                if spec.startswith("!"):
                    spec = spec.lstrip("!")
                sm.stop_background_keepalive()
                await _exec_raw(sm, spec, hold=False, verbose=verbose)
                sm.start_background_keepalive(interval=2.0)
                continue

            if cmd_lower.startswith("security "):
                parts = cmd.split()
                target = parts[1]
                algo_filter = parts[2:] if len(parts) > 2 else []
                sm.stop_background_keepalive()
                await _exec_security(sm, target, algo_filter, ecu_index, verbose)
                sm.start_background_keepalive(interval=2.0)
                continue

            # Track ATSH commands
            atsh_match = re.match(r"^ATSH\s*([0-9A-Fa-f]{3})$", cmd, re.IGNORECASE)
            if atsh_match:
                last_tx_id = int(atsh_match.group(1), 16)

            # Pause background keepalive during manual command
            sm.stop_background_keepalive()
            await sm.keepalive_stale()

            # Restore header if we know one
            if last_tx_id and not atsh_match:
                await terminal.set_header(last_tx_id)

            try:
                raw = await terminal.send_command(cmd)
                print(raw)

                response = parse_uds_response(raw)
                if response.get("ok") or response.get("nrc") is not None:
                    _last_response = response
                    if response.get("nrc") is not None:
                        nrc = response["nrc"]
                        desc = response.get("nrc_desc", "unknown")
                        print(f"  [NRC] 0x{nrc:02X} ({desc})")
            except ValueError as e:
                print(f"  !! {e}")
            except Exception as e:
                print(f"  Error: {e}")

            sm.start_background_keepalive(interval=2.0)

    finally:
        sm.stop_background_keepalive()
