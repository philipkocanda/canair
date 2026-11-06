"""What a signal's expression decodes to across a PID's saved captures."""

from __future__ import annotations

from dataclasses import dataclass

from canair.captures.store import load_pid_captures
from canair.expression.decoding import decode_payload
from canair.profile import Profile
from canair.uds.byteindex import payload_to_wican_bytes


@dataclass(frozen=True)
class CaptureRange:
    """What a signal's expression decodes to across the PID's existing captures."""

    captures: int
    displays: list[str]
    values: list[float]
    error: str | None


def capture_range(ecu: str, pid: str, name: str, param: dict, *, profile: Profile) -> CaptureRange:
    """Decode ``param`` over every saved capture of the PID; no printing."""
    caps = load_pid_captures(ecu, pid, profile=profile)
    displays: list[str] = []
    values: list[float] = []
    err: str | None = None
    for cap in caps:
        try:
            wb = payload_to_wican_bytes(cap["payload"])
        except Exception:
            continue
        decoded = decode_payload(wb, {name: param}).get(name)
        if not decoded:
            continue
        if decoded.get("error"):
            err = decoded["error"]
            continue
        if decoded.get("display") is not None:
            if decoded["display"] not in displays:
                displays.append(decoded["display"])
        elif decoded.get("value") is not None:
            values.append(decoded["value"])
    return CaptureRange(len(caps), displays, values, err)
