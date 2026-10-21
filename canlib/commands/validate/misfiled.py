"""captures/ validation — replies saved under the wrong ECU (cross-file pass).

The per-file lints in ``captures.py`` can't see this: a misfile only shows against the rest of
the corpus. Detection lives in ``canlib.misfiled``; this module walks the files and words it.
"""

from __future__ import annotations

from pathlib import Path

from canlib import capture_io
from canlib.misfiled import Misfile, PayloadSample, find_misfiled

from .captures import CaptureWarning


def _samples(files: list[Path]) -> tuple[list[PayloadSample], list[tuple[Path, int, int, str]]]:
    """Every hex payload capture, plus a ``(path, session, capture, time)`` locator per sample."""
    from canlib.ecus import parse_ecu_ref

    out: list[PayloadSample] = []
    where: list[tuple[Path, int, int, str]] = []
    for path in files:
        data = capture_io.load_capture_file(path)
        if not isinstance(data, dict):
            continue
        for si, session in enumerate(data.get("sessions", []) or []):
            if not isinstance(session, dict):
                continue
            for ci, cap in enumerate(session.get("captures", []) or []):
                if not isinstance(cap, dict):
                    continue
                rx = parse_ecu_ref(capture_io.capture_rx(cap) or None)
                pid, payload = cap.get("pid"), cap.get("payload")
                if rx is None or not pid or not payload:
                    continue
                try:
                    raw = bytes.fromhex(str(payload))
                except ValueError:
                    continue  # the non-hex lint reports it
                out.append(PayloadSample(len(out), rx, str(pid), raw))
                where.append((path, si, ci, str(cap.get("time", "?"))))
    return out, where


def _message(m: Misfile, names: dict[int, str]) -> str:
    own = names.get(m.sample.rx, hex(m.sample.rx))
    likely = names.get(m.likely_rx, hex(m.likely_rx))
    pid = m.sample.pid
    if m.length_mismatch:
        evidence = (
            f"is {len(m.sample.payload)} bytes like {likely}'s {pid} replies "
            f"({m.likely_samples} captures), not {own}'s usual {m.own_length}"
        )
        if m.identical:
            evidence += f", and byte-identical to one of {likely}'s"
    else:
        evidence = f"is byte-identical to a {likely} {pid} reply"
    return (
        f"payload {evidence} — probably {likely}'s reply misfiled under {own}; "
        f"check it with `canair captures uds {own}:{pid} --diff --all` and remove it with "
        "`captures uds --delete`"
    )


def misfile_warnings(
    files: list[Path], names: dict[int, str] | None = None
) -> dict[Path, list[CaptureWarning]]:
    """Per-file warnings for captures that look like another ECU's reply.

    ``names`` maps an RX address to an ECU name for the message; it defaults to the active
    profile's registry.
    """
    if names is None:
        from canlib.ecus import build_rx_index

        names = build_rx_index()
    out: dict[Path, list[CaptureWarning]] = {}
    samples, where = _samples(files)
    for m in find_misfiled(samples):
        path, si, ci, time = where[m.sample.handle]
        own = names.get(m.sample.rx, hex(m.sample.rx))
        location = f"sessions[{si}].captures[{ci}] ({own} {m.sample.pid} @ {time})"
        out.setdefault(path, []).append(CaptureWarning(location, _message(m, names)))
    return out
