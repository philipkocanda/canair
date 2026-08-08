"""Multi-ECU pipeline — sub-command parsing and ECU/PID token resolution.

Device-free parsing helpers for :mod:`canlib.modes.multi`: resolving an ECU
name/hex to a TX id, recognising a bare PID token, expanding a ``query`` step
into ``(ecu, pids)`` pairs, normalising overlapping selectors, and turning the
raw sub-command strings into structured dicts. Kept separate from the
execution/orchestration layers so the parsing can be unit-tested and reused
without importing the transport stack. The ECU registry is consulted for
name/alias canonicalisation only, and its absence is tolerated.
"""

import shlex

from ..query import looks_like_pid


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


# Backwards-compatible alias — the space-vs-colon guard now lives in
# canlib.query.looks_like_pid (shared with the capture query hint).
_looks_like_pid = looks_like_pid


def _query_selectors(tokens: list[str]) -> list[tuple[str, list[str]]]:
    """Expand ``query`` sub-command tokens into ``(ecu, pids)`` pairs.

    Tokens are parsed with the ECU/PID mini-language (canlib.query): each
    whitespace-separated ``ECU[:PID,PID,...]`` selector becomes one pair (a bare
    ECU yields an empty PID list = all PIDs). Identical selectors are de-duped so
    a repeated ECU/PID isn't polled twice. Raises ``QueryError`` (a ``ValueError``)
    on malformed input.

    This de-dup is *exact-match, within one step*; overlapping selectors spread
    across steps (``IGPM AAF @driving``) are coalesced afterwards by
    :func:`normalize_query_steps`.

    Fails loudly on the classic space-vs-colon mistake: a bare selector that
    looks like a PID/DID (e.g. ``query IGPM 22BC07``, meant to be
    ``query IGPM:22BC07``) is rejected rather than silently treated as a query
    for a non-existent ECU named ``22BC07``. An explicit ``0x``-prefixed token
    (``query 0x770``) is exempt — it's an unambiguous, deliberate hex TX-id
    selector, not a PID stranded from its ECU.
    """
    from ..query import parse_query

    query = parse_query(tokens)
    prev_ecu: str | None = None
    for sel in query.selectors:
        if not sel.pids and sel.ecu.startswith("0X"):
            # Explicit hex TX-id selector (e.g. `query 0x770`) — deliberate, keep.
            prev_ecu = sel.ecu
            continue
        if not sel.pids and looks_like_pid(sel.ecu):
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


def selector_text(step: dict) -> str:
    """Render a parsed ``query`` step back as its mini-language selector.

    ``{"ecu": "AAF", "pids": ["2180"]}`` -> ``"AAF:2180"``; a step with no PIDs
    (all of them) renders as the bare ECU name. Used to report which original
    selectors a normalised step absorbed.
    """
    pids = step.get("pids") or []
    return f"{step['ecu']}:{','.join(pids)}" if pids else step["ecu"]


def _canonical_name_index() -> dict[str, str] | None:
    """The ECU name/alias index, or ``None`` when no registry is available."""
    from ..ecus import build_canonical_name_index

    try:
        return build_canonical_name_index()
    except FileNotFoundError:
        return None


def _merge_query_run(run: list[dict], name_index: dict[str, str] | None) -> list[dict]:
    """Coalesce one contiguous run of ``query`` steps to one step per ECU.

    ECU names are canonicalised first, so an alias selector (``LDC``) merges with
    its canonical form (``OBC``) instead of becoming a second, unresolvable step.
    PID lists are unioned in first-appearance order, and a **bare** ECU selector
    (empty ``pids`` = all PIDs) absorbs every PID-specific selector for that ECU
    rather than sitting beside it.

    A step records ``merged_from`` only when a merge was genuinely *redundant* —
    an exact repeat, or a selector subsumed by a bare one — since that is the
    mistake worth reporting. Combining two distinct PID selectors on one ECU
    (``BMS:2101`` + ``BMS:2105``) is normal and stays silent, and an unmerged step
    is returned byte-identical (no extra key).
    """
    from ..ecus import canonical_ecu_name_safe

    merged: dict[str, dict] = {}
    sources: dict[str, list[str]] = {}
    overlapped: set[str] = set()
    order: list[str] = []
    for step in run:
        canon = canonical_ecu_name_safe(step["ecu"], name_index).upper()
        pids = list(step.get("pids") or [])
        prev = merged.get(canon)
        if prev is None:
            merged[canon] = {"type": "query", "ecu": canon, "pids": pids}
            sources[canon] = [selector_text(step)]
            order.append(canon)
            continue
        sources[canon].append(selector_text(step))
        if not prev["pids"]:  # already "all PIDs" — nothing an extra selector adds
            overlapped.add(canon)
            continue
        if not pids:  # a bare selector widens to all PIDs and subsumes the earlier ones
            prev["pids"] = []
            overlapped.add(canon)
            continue
        fresh = [p for p in pids if p not in prev["pids"]]
        if not fresh:
            overlapped.add(canon)
        prev["pids"].extend(fresh)

    out = []
    for canon in order:
        step = merged[canon]
        if canon in overlapped:
            step["merged_from"] = sources[canon]
        out.append(step)
    return out


def normalize_query_steps(
    commands: list[dict], name_index: dict[str, str] | None = None
) -> list[dict]:
    """Canonicalise ECU names and coalesce overlapping ``query`` selectors.

    Without this, ``canair mon IGPM OBC AAF @driving`` polls IGPM and AAF twice:
    ``@driving`` already contains them, but each positional STEP is parsed
    independently (so :func:`_query_selectors`' within-step de-dup never sees the
    other step) and a bare ``AAF`` is a different key from ``AAF:2180``. The
    duplicates render as duplicate ECU blocks, collide on every
    ``(ecu_label, pid)``-keyed structure in the monitor, and double the poll
    rounds for the affected ECUs.

    Merging is scoped to each **contiguous run** of ``query`` steps, so a
    deliberate pipeline re-read (``read BMS:2101 "sleep 5" BMS:2101``) keeps both
    of its reads: the intervening step ends the run. Non-query steps pass through
    untouched, and run order is preserved (an ECU keeps its first position).

    ``name_index`` is the ECU name/alias map; it is loaded from the active profile
    when omitted, and a missing registry degrades to plain upper-casing.
    """
    if name_index is None:
        name_index = _canonical_name_index()
    out: list[dict] = []
    run: list[dict] = []
    for cmd in commands:
        if cmd["type"] == "query":
            run.append(cmd)
            continue
        out.extend(_merge_query_run(run, name_index))
        run = []
        out.append(cmd)
    out.extend(_merge_query_run(run, name_index))
    return out


def merged_selector_notes(commands: list[dict]) -> list[str]:
    """One ``ECU <- sel, sel`` line per step that absorbed a redundant selector.

    Repeats collapse to ``sel x2``, so an accidentally doubled ECU reads as such
    rather than as the same name twice.
    """
    from collections import Counter

    notes = []
    for cmd in commands:
        if not cmd.get("merged_from"):
            continue
        counts = Counter(cmd["merged_from"])
        parts = [s if n == 1 else f"{s} \u00d7{n}" for s, n in counts.items()]
        notes.append(f"{cmd['ecu']} \u2190 {', '.join(parts)}")
    return notes


def parse_sub_commands(args: list[str]) -> list[dict]:
    """Parse multi-mode sub-command strings into structured dicts.

    Each string is a mini-command like 'skm-wake acc' or 'raw 770:22BC03'.
    Overlapping ``query`` selectors are coalesced afterwards — see
    :func:`normalize_query_steps`.
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

    return normalize_query_steps(commands)
