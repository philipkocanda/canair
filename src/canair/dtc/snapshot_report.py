"""Text rendering of the snapshots :mod:`.snapshots` attached to a read result."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from canair.profile import Profile
from canair.render import ansi
from canair.render.formatting import format_value


def snapshot_lines(code: Mapping[str, Any], indent: str) -> list[str]:
    """The lines printed under one code for its ``snapshots`` (none when it has none)."""
    lines: list[str] = []
    for snap in code.get("snapshots") or ():
        head = f"{indent}snapshot {snap['record']:02X}"
        if snap.get("error"):
            lines.append(f"{head}  {ansi.YELLOW}{snap['error']}{ansi.RESET}")
            continue
        if not snap.get("dids") and not snap.get("unresolved_did"):
            lines.append(f"{head}  {ansi.DIM}(empty){ansi.RESET}")
            continue
        for did in snap.get("dids") or ():
            lines.append(f"{head}  DID {did['did']}")
            lines.extend(_did_lines(did, indent + "  "))
        if snap.get("unresolved_did"):
            lines.append(
                f"{head}  DID {snap['unresolved_did']}  {ansi.YELLOW}length unknown{ansi.RESET}"
                f"  {snap['undecoded']}"
            )
    return lines


def _did_lines(did: Mapping[str, Any], indent: str) -> list[str]:
    signals = did.get("signals")
    if signals is None:
        return [f"{indent}{ansi.DIM}not defined ({did['pid']}):{ansi.RESET} {did['data']}"]
    if not signals:
        return [f"{indent}{ansi.DIM}no signals decoded:{ansi.RESET} {did['data']}"]
    width = max(len(s["name"]) for s in signals)
    out = []
    for s in signals:
        if s.get("error"):
            value = f"{ansi.RED}✗ {s['error']}{ansi.RESET}"
        else:
            value = format_value(s["value"], s.get("unit", ""), s.get("display", ""))
        out.append(f"{indent}{s['name']:<{width}}  {value}")
    return out


def undefined_hints(results: Iterable[Mapping[str, Any]], *, profile: Profile) -> list[str]:
    """One "define it" hint per snapshot DID the profile has no signals for."""
    from canair.ecus.names import ecu_name

    seen: dict[tuple[str, str], int] = {}
    for result in results:
        ecu = ecu_name(int(result["tx"], 16), profile=profile)
        for code in result.get("dtcs") or ():
            for snap in code.get("snapshots") or ():
                for did in snap.get("dids") or ():
                    if did.get("signals") is None:
                        seen.setdefault((ecu, did["pid"]), len(did["data"]) // 2)
    return [
        f"  {ansi.DIM}Snapshot DID {pid[2:]} is not defined: `canair pids add-pid {ecu} {pid}`, "
        f"then `canair pids set-snapshot-only {ecu} {pid} --length {length}` if a live read "
        f"of it is refused.{ansi.RESET}"
        for (ecu, pid), length in seen.items()
    ]


def status_line(result: Mapping[str, Any]) -> str | None:
    """Why an ECU gave no snapshots (``snapshot_status``), for the verbose output."""
    status = result.get("snapshot_status")
    if not status:
        return None
    return f"  {ansi.DIM}Snapshots: not available ({status['detail']}){ansi.RESET}"


def logged_lines(
    ecu: str | None,
    records: list[Mapping[str, Any]] | None,
    indent: str,
    previous: Mapping[str, Any] | None = None,
    previous_records: list[Mapping[str, Any]] | None = None,
    *,
    profile: Profile,
) -> list[str]:
    """A logged code's snapshots, decoded now, noting when the previous scan read the same."""
    if not records:
        return []
    from canair.dtc.snapshot_decode import decode_logged

    lines = []
    if previous is not None and _replies(previous_records) == _replies(records):
        stamp = previous.get("timestamp", "?")
        lines.append(f"{indent}{ansi.DIM}snapshots unchanged since the scan of {stamp}{ansi.RESET}")
    return lines + snapshot_lines(
        {"snapshots": decode_logged(ecu, list(records), profile=profile)}, indent
    )


def _replies(records: Iterable[Mapping[str, Any]] | None) -> list[tuple[Any, str]]:
    return [(r.get("record"), str(r.get("hex", "")).upper()) for r in records or ()]
