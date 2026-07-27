"""Multi-ECU pipeline — sub-command parsing and ECU/PID token resolution.

Pure, device-free parsing helpers for :mod:`canlib.modes.multi`: resolving an
ECU name/hex to a TX id, recognising a bare PID token, expanding a ``query``
step into ``(ecu, pids)`` pairs, and turning the raw sub-command strings into
structured dicts. Kept separate from the execution/orchestration layers so the
parsing can be unit-tested and reused without importing the transport stack.
"""

import shlex


def resolve_tx_id(name_or_hex: str, ecu_index: dict) -> int | None:
    """Resolve an ECU name or hex TX ID to an integer.

    Accepts: 'IGPM', 'igpm', '770', '0x770', '7A0', and ECU-registry aliases
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
                f"iocontrol, repl"
            )

    return commands
