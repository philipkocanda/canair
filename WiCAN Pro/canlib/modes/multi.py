"""Multi-ECU pipeline mode.

Executes a sequence of sub-commands within a single WebSocket session,
managing extended diagnostic sessions across multiple ECUs with interleaved
TesterPresent keepalives.

Sub-commands:
    skm-wake [level]                Wake SKM + activate relay (acc/ign1/ign2)
    session <ECU|TX_ID> [--wake]    Enter extended session on ECU
    query <ECU> [PID ...]           Query ECU parameters (like --ecu/--param)
    raw <TX:PID>                    Raw UDS request
    scan <TX> <SVC> <RANGE> [APPEND]  Scan PID range
    sleep <seconds>                 Pause between steps
    repl                            Drop into interactive REPL (explicit)

After all sub-commands complete, exits by default. Use --repl to drop into
an interactive REPL, or include an explicit 'repl' step in the pipeline.
"""

import asyncio
import re
import shlex

from ..session_manager import SessionManager
from ..pids import build_ecu_index, build_param_index, load_pids
from ..constants import PIDS_DIR
from ..formatting import (
    print_decoded_params,
    print_ecu_results,
    print_hexdump,
    print_json_result,
    decode_uds_response,
)
from ..expression import evaluate_expression
from ..elm327 import parse_elm_response, elm_hex_to_wican_bytes
from ..terminal import WiCANTerminal


def resolve_tx_id(name_or_hex: str, ecu_index: dict) -> int | None:
    """Resolve an ECU name or hex TX ID to an integer.

    Accepts: 'IGPM', 'igpm', '770', '0x770', '7A0'.
    """
    upper = name_or_hex.upper()
    if upper in ecu_index:
        return ecu_index[upper]["tx_id"]

    # Try as hex
    cleaned = upper.removeprefix("0X")
    try:
        return int(cleaned, 16)
    except ValueError:
        return None


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
                raise ValueError(
                    f"'session' requires an ECU name or TX ID: session IGPM"
                )
            wake = "--wake" in parts
            target = parts[1]
            commands.append({"type": "session", "target": target, "wake": wake})

        elif verb == "query":
            if len(parts) < 2:
                raise ValueError(f"'query' requires an ECU name: query IGPM BC03 BC06")
            ecu = parts[1]
            pids = parts[2:] if len(parts) > 2 else []
            commands.append({"type": "query", "ecu": ecu, "pids": pids})

        elif verb == "raw":
            if len(parts) < 2:
                raise ValueError(f"'raw' requires TX:PID: raw 770:22BC03")
            commands.append(
                {"type": "raw", "spec": parts[1], "hold": "--hold" in parts}
            )

        elif verb == "scan":
            # scan <TX> <SVC> <RANGE> [APPEND]
            if len(parts) < 4:
                raise ValueError(
                    f"'scan' requires: scan <TX> <SERVICE> <RANGE> [APPEND]"
                )
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
                raise ValueError(
                    "'security' requires an ECU name or TX ID: security BCM"
                )
            target = parts[1]
            algos = parts[2:] if len(parts) > 2 else []
            commands.append({"type": "security", "target": target, "algos": algos})

        elif verb == "repl":
            commands.append({"type": "repl"})

        else:
            raise ValueError(
                f"Unknown sub-command: {verb!r}. "
                f"Available: skm-wake, session, query, raw, scan, sleep, "
                f"security, repl"
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


async def _exec_session(sm: SessionManager, target: str, wake: bool, ecu_index: dict):
    """Execute session sub-command."""
    tx_id = resolve_tx_id(target, ecu_index)
    if tx_id is None:
        print(f"  ERROR: Unknown ECU '{target}'. Use a name (IGPM) or hex ID (770).")
        return False
    print(f"  Opening extended session on 0x{tx_id:03X} ({target})...")
    return await sm.open_session(tx_id, wake=wake)


async def _exec_query(
    sm: SessionManager,
    ecu_name_str: str,
    pid_filter: list[str],
    ecu_index: dict,
    pids_data: dict,
    verbose: bool,
    return_results: bool = False,
):
    """Execute query sub-command — query ECU parameters.

    Args:
        return_results: If True, return (ecu_label, pid_results) instead of printing.
    """
    upper = ecu_name_str.upper()
    if upper not in ecu_index:
        print(
            f"  ERROR: Unknown ECU '{ecu_name_str}'. Available: {', '.join(ecu_index.keys())}"
        )
        return

    ecu_info = ecu_index[upper]
    tx_id = ecu_info["tx_id"]

    # Refresh stale sessions before switching
    await sm.keepalive_stale()
    await sm.terminal.set_header(tx_id)

    # Open session on this ECU if not already tracked
    if not sm.has_session(tx_id):
        # Check if ECU needs session (heuristic: try without first)
        pass

    pids_to_query = ecu_info["pids"]
    raw_pids = []  # Unmatched filters to query as raw UDS requests
    if pid_filter:
        # Match filter values flexibly: "BC03" matches key "22BC03", and "22BC03" matches too
        filter_upper = [p.upper() for p in pid_filter]
        pids_to_query = {
            k: v
            for k, v in pids_to_query.items()
            if k.upper() in filter_upper
            or any(k.upper().endswith(f) for f in filter_upper)
        }

        # Find unmatched filters — query them as raw UDS requests
        matched_filters = set()
        for f in filter_upper:
            for k in pids_to_query:
                if k.upper() == f or k.upper().endswith(f):
                    matched_filters.add(f)
                    break
        unmatched = [f for f in filter_upper if f not in matched_filters]
        if unmatched:
            # Convert short DID codes to full UDS request codes
            for u in unmatched:
                if all(c in "0123456789ABCDEF" for c in u):
                    if len(u) <= 2:
                        # Short KWP2000 local ID: "01" → "2101"
                        raw_pids.append(f"21{u}")
                    elif len(u) == 4 and u[:2] in ("21", "22"):
                        # Already a full service+ID: "2101", "22B0"
                        raw_pids.append(u)
                    elif len(u) == 4:
                        # 4-char DID: "B001" → "22B001"
                        raw_pids.append(f"22{u}")
                    elif len(u) >= 5 and u[:2] in ("21", "22"):
                        # Full request code: "22BC03", "2101"
                        raw_pids.append(u)
                    else:
                        raw_pids.append(f"22{u}")
                else:
                    print(f"  WARNING: Invalid PID format '{u}', skipping")
            if raw_pids:
                print(
                    f"  NOTE: {', '.join(raw_pids)} not in {PIDS_DIR.name}/ — querying raw"
                )

        if not pids_to_query and not raw_pids:
            print(f"  No matching PIDs for filter: {pid_filter}")
            print(f"  Available: {', '.join(sorted(ecu_info['pids'].keys()))}")
            return

    total = len(pids_to_query) + len(raw_pids)

    # Build sorted query plan: interleave mapped and unmapped PIDs by DID
    query_plan = []  # list of (pid_code, pid_info_or_None, unmapped)
    for pid_code, pid_info in pids_to_query.items():
        query_plan.append((pid_code, pid_info, False))
    for raw_pid in raw_pids:
        query_plan.append((raw_pid, None, True))
    query_plan.sort(key=lambda x: x[0])

    all_pid_results = []

    for pid_code, pid_info, unmapped in query_plan:
        await sm.keepalive_stale()
        await sm.terminal.set_header(tx_id)

        resp = await sm.terminal.send_uds(pid_code)
        if not resp.get("ok"):
            error = resp.get("error") or resp.get("nrc_desc", "unknown")
            nrc = resp.get("nrc")
            if nrc is not None:
                error = f"NRC 0x{nrc:02X} ({resp['nrc_desc']})"
            all_pid_results.append(
                {"pid": pid_code, "error": error, "unmapped": unmapped}
            )
            continue

        if pid_info:
            # Mapped PID — decode parameters
            wican_bytes = elm_hex_to_wican_bytes(resp["hex"])
            params = pid_info["parameters"]
            results = []
            for pname, pdef in params.items():
                expr = pdef.get("expression", "")
                unit = pdef.get("unit", "")
                verified = pdef.get("verified", False)
                display = pdef.get("display", "")
                if not expr:
                    continue
                try:
                    value = evaluate_expression(expr, wican_bytes)
                    value = round(value * 100) / 100
                    results.append((pname, value, unit, expr, None, verified, display))
                except Exception as e:
                    results.append((pname, None, unit, expr, str(e), verified, display))

            all_pid_results.append(
                {
                    "pid": pid_code,
                    "params": results,
                    "raw_hex": resp["hex"],
                }
            )
        else:
            # Unmapped PID — raw response
            decode = decode_uds_response(resp["bytes"])
            all_pid_results.append(
                {
                    "pid": pid_code,
                    "params": [],
                    "raw_hex": resp["hex"],
                    "decode": decode,
                    "unmapped": True,
                }
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
        print(
            f"  ERROR: Invalid raw format: {spec}. Expected: TX:PID (e.g., 770:22BC03)"
        )
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
        bg = sm.start_background_keepalive()
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("  Continuing...")
        finally:
            sm.stop_background_keepalive()


async def _exec_scan(
    sm: SessionManager,
    tx_str: str,
    service_str: str,
    range_str: str,
    append: str,
    verbose: bool,
):
    """Execute scan sub-command."""
    from .scan import mode_scan

    tx_id = int(tx_str, 16)
    service = int(service_str, 16)
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
            ((s & 0xFF) << 24)
            | ((s & 0xFF00) << 8)
            | ((s & 0xFF0000) >> 8)
            | ((s >> 24) & 0xFF)
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
        for attempt in range(3):
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
            desc = (
                resp.get("nrc_desc") or resp.get("error", "unknown")
                if resp
                else "no response"
            )
            print(f"  {algo_name:<30} {'—':>10}  {'—':>10}  Seed failed: {desc}")
            continue

        # Parse seed from response bytes (67 01 SS SS SS SS)
        raw_bytes = resp.get("bytes", b"")
        if len(raw_bytes) < 6 or raw_bytes[0] != 0x67 or raw_bytes[1] != 0x01:
            print(
                f"  {algo_name:<30} {'—':>10}  {'—':>10}  Bad seed response: {resp.get('hex', '?')}"
            )
            continue

        seed_bytes = raw_bytes[2:6]
        seed = int.from_bytes(seed_bytes, "big")
        seed_hex = f"{seed:08X}"

        # Compute key
        key = algo_fn(seed)
        key_hex = f"{key:08X}"

        # Send key
        key_resp = await sm.terminal.send_uds(f"2702{key_hex}", timeout=5.0)

        if key_resp.get("ok"):
            raw_key_bytes = key_resp.get("bytes", b"")
            if (
                len(raw_key_bytes) >= 2
                and raw_key_bytes[0] == 0x67
                and raw_key_bytes[1] == 0x02
            ):
                print(
                    f"  {algo_name:<30} {seed_hex:>10}  {key_hex:>10}  *** ACCEPTED ***"
                )
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
                print(
                    f"  {algo_name:<30} {seed_hex:>10}  {key_hex:>10}  LOCKOUT — stopping."
                )
                return False
            elif nrc == 0x37:  # requiredTimeDelayNotExpired
                print(
                    f"  {algo_name:<30} {seed_hex:>10}  {key_hex:>10}  delay penalty (will retry next)"
                )
            else:
                desc = key_resp.get("nrc_desc") or key_resp.get("error", "unknown")
                print(f"  {algo_name:<30} {seed_hex:>10}  {key_hex:>10}  {desc}")

    print(f"\n  No algorithm worked. Security access denied.")
    return False


async def mode_multi(
    terminal: WiCANTerminal,
    sub_commands: list[str],
    pids_data: dict,
    verbose: bool,
    no_repl: bool = False,
):
    """Execute a multi-ECU pipeline and optionally drop into REPL.

    Args:
        terminal: Connected WiCANTerminal.
        sub_commands: List of sub-command strings (e.g., ["skm-wake acc", "query IGPM BC03"]).
        pids_data: Loaded PID definitions.
        verbose: Show debug output.
        no_repl: If True, don't drop into REPL after pipeline.
    """
    commands = parse_sub_commands(sub_commands)
    ecu_index = build_ecu_index(pids_data)
    sm = SessionManager(terminal, verbose=verbose)
    repl_executed = False

    try:
        for i, cmd in enumerate(commands):
            cmd_type = cmd["type"]
            step = f"[{i + 1}/{len(commands)}]"

            if cmd_type == "skm-wake":
                print(f"\n{step} SKM wakeup ({cmd['level']})...")
                await _exec_skm_wake(sm, cmd["level"], verbose)

            elif cmd_type == "session":
                print(f"\n{step} Session on {cmd['target']}...")
                await _exec_session(sm, cmd["target"], cmd["wake"], ecu_index)

            elif cmd_type == "query":
                pids_str = " ".join(cmd["pids"]) if cmd["pids"] else "all"
                print(f"\n{step} Query {cmd['ecu']} ({pids_str})...")
                await _exec_query(
                    sm, cmd["ecu"], cmd["pids"], ecu_index, pids_data, verbose
                )

            elif cmd_type == "raw":
                print(f"\n{step} Raw {cmd['spec']}...")
                await _exec_raw(sm, cmd["spec"], cmd["hold"], verbose)

            elif cmd_type == "scan":
                print(
                    f"\n{step} Scan {cmd['tx']} service {cmd['service']} range {cmd['range']}..."
                )
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
                await _exec_security(
                    sm, cmd["target"], cmd["algos"], ecu_index, verbose
                )

            elif cmd_type == "repl":
                print(f"\n{step} Entering REPL...")
                repl_executed = True
                await _multi_repl(sm, ecu_index, pids_data, verbose)

        # Auto-REPL if no explicit repl step and --repl was passed
        if not repl_executed and not no_repl:
            sessions_str = ", ".join(f"0x{tx:03X}" for tx in sm.active_sessions)
            if sessions_str:
                print(f"\n  Active sessions: {sessions_str}")
            print(f"\n  Pipeline complete. Entering REPL...")
            await _multi_repl(sm, ecu_index, pids_data, verbose)

    except KeyboardInterrupt:
        print("\n  Interrupted.")

    finally:
        sm.stop_background_keepalive()
        print("  Closing all sessions...")
        try:
            await asyncio.wait_for(sm.close_all(), timeout=3.0)
        except (asyncio.TimeoutError, KeyboardInterrupt, Exception):
            pass


async def _multi_repl(
    sm: SessionManager, ecu_index: dict, pids_data: dict, verbose: bool
):
    """Interactive REPL with multi-ECU session awareness.

    Extends the standard REPL with session keepalives and multi-ECU commands.
    """
    from .interactive import mode_interactive
    from .skm_wakeup import mode_skm_wakeup
    from .tester import mode_tester_present
    from .identity import mode_identity

    terminal = sm.terminal
    param_index = build_param_index(pids_data)
    last_response = None
    last_tx_id = None

    # Start background keepalive for all tracked sessions
    bg_task = sm.start_background_keepalive(interval=2.0)

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

        import sys, signal

        # Set up SIGINT to cancel the current readline gracefully
        repl_quit = asyncio.Event()
        old_handler = signal.getsignal(signal.SIGINT)

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
                    bg_task = sm.start_background_keepalive(interval=2.0)
                    print(f"  Session opened on 0x{tx_id:03X}")
                continue

            if cmd_lower.startswith("skm"):
                parts = cmd.split()
                level = parts[1] if len(parts) > 1 else "acc"
                sm.stop_background_keepalive()
                await mode_skm_wakeup(terminal, level, verbose)
                sm._sessions[0x7A5] = __import__("time").monotonic()
                bg_task = sm.start_background_keepalive(interval=2.0)
                continue

            if cmd_lower.startswith("query "):
                parts = cmd.split()
                # First token might be "query" or "!query"
                ecu = parts[1]
                pids = parts[2:] if len(parts) > 2 else []
                sm.stop_background_keepalive()
                await _exec_query(sm, ecu, pids, ecu_index, pids_data, verbose)
                bg_task = sm.start_background_keepalive(interval=2.0)
                continue

            if cmd_lower.startswith("raw "):
                spec = cmd.split(None, 1)[1]
                # Strip leading ! if present
                if spec.startswith("!"):
                    spec = spec.lstrip("!")
                sm.stop_background_keepalive()
                await _exec_raw(sm, spec, hold=False, verbose=verbose)
                bg_task = sm.start_background_keepalive(interval=2.0)
                continue

            if cmd_lower.startswith("security "):
                parts = cmd.split()
                target = parts[1]
                algo_filter = parts[2:] if len(parts) > 2 else []
                sm.stop_background_keepalive()
                await _exec_security(sm, target, algo_filter, ecu_index, verbose)
                bg_task = sm.start_background_keepalive(interval=2.0)
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

                response = parse_elm_response(raw)
                if response.get("ok") or response.get("nrc") is not None:
                    last_response = response
                    if response.get("nrc") is not None:
                        nrc = response["nrc"]
                        desc = response.get("nrc_desc", "unknown")
                        print(f"  [NRC] 0x{nrc:02X} ({desc})")
            except ValueError as e:
                print(f"  !! {e}")
            except Exception as e:
                print(f"  Error: {e}")

            bg_task = sm.start_background_keepalive(interval=2.0)

    finally:
        sm.stop_background_keepalive()
