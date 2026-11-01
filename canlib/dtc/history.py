"""What the DTC log says now: each ECU's open codes, and the log's totals.

An ECU's **open** codes are the ones its most recent admitted scan read (see
:class:`canlib.dtc.scope.Car`) — a single-ECU scan of it, or a full sweep that read it.
A full sweep lists only the ECUs that had codes, so one it read but did not list was
clean; one it lists under ``unread:`` says nothing and the search goes further back. A
``--clear`` leaves the ECU clean until it is read again, so cleared codes are not open.

Read-only and offline: :func:`ecu_status` backs ``canair ecu <ECU>``, :func:`log_stats`
the hints on a bare ``canair dtc`` and in ``canair config``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from canlib.dtc.log import load_log
from canlib.dtc.refs import EcuRefs
from canlib.dtc.scope import ALL_SCOPE, Car, current_car


@dataclass(frozen=True)
class EcuStatus:
    """One ECU as its latest admitted reading (or clear) left it."""

    rx: str
    ecu: str  # the ECU as people name it (resolved from the profile when read)
    codes: tuple[str, ...]
    timestamp: str
    label: str | None = None
    cleared: bool = False  # the latest event was a `--clear`, not a reading

    def as_dict(self) -> dict[str, Any]:
        return {
            "ecu": self.ecu,
            "rx": self.rx,
            "open": list(self.codes),
            "scanned": self.timestamp,
            "label": self.label,
            "cleared": self.cleared,
        }


def _read_in(entry: Mapping[str, Any], rx: str) -> tuple[str, ...] | None:
    """The codes ``entry`` read from ``rx``, or None when it did not read it."""
    ecus = entry.get("ecus") or {}
    if rx in ecus:
        return tuple(str(c) for c in (ecus[rx] or {}).get("dtcs") or [])
    if entry.get("scope") != ALL_SCOPE or rx in (entry.get("unread") or []):
        return None
    return ()


def _events(data: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    """``(timestamp, kind, entry)`` for every scan and clear, newest first."""
    events = [
        (str(e.get("timestamp", "")), kind, e)
        for kind in ("scans", "clears")
        for e in data.get(kind) or []
        if isinstance(e, Mapping)
    ]
    # A clear and the scan it followed can share a second: the clear is the later event.
    return sorted(events, key=lambda ev: (ev[0], ev[1] == "clears"), reverse=True)


def ecu_status(
    rx: str,
    *,
    car: Car | None = None,
    data: Mapping[str, Any] | None = None,
    refs: EcuRefs | None = None,
) -> EcuStatus | None:
    """The ECU ``rx``'s codes after its latest admitted reading or clear, or None when
    neither ever happened."""
    car, refs = car or current_car(), refs or EcuRefs.current()
    data = data if data is not None else load_log()
    name = refs.name(rx)
    for timestamp, kind, entry in _events(data):
        if kind == "clears":
            if entry.get("rx") != rx:
                continue
            codes: tuple[str, ...] | None = ()
        elif entry.get("scope") in (ALL_SCOPE, rx):
            codes = _read_in(entry, rx)
        else:
            continue
        if codes is None or not car.admits(entry, [name] if name else []):
            continue
        return EcuStatus(
            rx=rx,
            ecu=refs.label(rx),
            codes=codes,
            timestamp=timestamp or "?",
            label=entry.get("label"),
            cleared=kind == "clears",
        )
    return None


def logged_ecus(data: Mapping[str, Any]) -> list[str]:
    """Every ECU (rx) any scan recorded a reading for, sorted."""
    return sorted(
        {str(rx) for e in data.get("scans") or [] for rx in ((e or {}).get("ecus") or {})}
    )


def open_dtcs(*, car: Car | None = None, data: Mapping[str, Any] | None = None) -> list[EcuStatus]:
    """Every ECU whose latest admitted reading still has codes."""
    car, refs = car or current_car(), EcuRefs.current()
    data = data if data is not None else load_log()
    out = []
    for rx in logged_ecus(data):
        status = ecu_status(rx, car=car, data=data, refs=refs)
        if status is not None and status.codes:
            out.append(status)
    return out


@dataclass(frozen=True)
class LogStats:
    """Totals of one DTC log, as seen from one car."""

    path: Path
    scans: int  # scans admitted for the car
    clears: int
    seen: int  # distinct (ECU, code) pairs those scans ever recorded
    open: list[EcuStatus]
    last_scan: str | None
    other_cars: int  # scans recorded for another car (or revision) and left out
    unnamed: bool  # several variants, none selected

    @property
    def open_codes(self) -> int:
        return sum(len(s.codes) for s in self.open)

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": str(self.path),
            "scans": self.scans,
            "clears": self.clears,
            "dtcs_seen": self.seen,
            "open": [s.as_dict() for s in self.open],
            "open_codes": self.open_codes,
            "last_scan": self.last_scan,
            "other_car_scans": self.other_cars,
        }


def log_stats(path: Path | None = None, *, car: Car | None = None) -> LogStats:
    """Totals of the DTC log at ``path`` (default: the active profile's) for ``car``."""
    from canlib.dtc.log import log_path

    car = car or current_car()
    p = log_path(path)
    data = load_log(p)
    scans = [e for e in data.get("scans") or [] if isinstance(e, Mapping)]
    admitted = [e for e in scans if car.admits(e)]
    clears = [e for e in data.get("clears") or [] if isinstance(e, Mapping) and car.admits(e)]
    seen = {
        (str(rx), str(code))
        for entry in admitted
        for rx, info in (entry.get("ecus") or {}).items()
        for code in (info or {}).get("dtcs") or []
    }
    return LogStats(
        path=p,
        scans=len(admitted),
        clears=len(clears),
        seen=len(seen),
        open=open_dtcs(car=car, data=data),
        last_scan=str(admitted[-1].get("timestamp")) if admitted else None,
        other_cars=len(scans) - len(admitted),
        unnamed=car.unnamed,
    )
