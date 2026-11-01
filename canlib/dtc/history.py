"""What the DTC log says now: each ECU's open codes, and the log's totals.

An ECU's **open** codes are the ones its most recent admitted scan read (see
:class:`canlib.dtc.scope.Car`) — a single-ECU scan of it, or a full sweep that read it.
A full sweep lists only the ECUs that had codes, so one it read but did not list was
clean; one it lists under ``unread:`` says nothing and the search goes further back. A
``--clear`` logs a clean scan of the ECU, so cleared codes are not open.

Read-only and offline: :func:`ecu_status` backs ``canair ecu <ECU>``, :func:`log_stats`
the hints on a bare ``canair dtc`` and in ``canair config``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from canlib.dtc.log import load_log
from canlib.dtc.scope import ALL_SCOPE, Car, current_car, display_name, display_tx, scope_key


@dataclass(frozen=True)
class EcuStatus:
    """One ECU as its latest admitted scan read it."""

    tx: int
    ecu: str  # the display label the scan recorded
    codes: tuple[str, ...]
    timestamp: str
    label: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ecu": self.ecu,
            "tx": f"0x{self.tx:03X}",
            "open": list(self.codes),
            "scanned": self.timestamp,
            "label": self.label,
        }


def _read_in(entry: Mapping[str, Any], tx_id: int) -> tuple[str, tuple[str, ...]] | None:
    """(display, codes) when ``entry`` read the ECU ``tx_id``, else None."""
    for display, info in (entry.get("ecus") or {}).items():
        info = info or {}
        tx = display_tx(str(info.get("tx") or "")) or display_tx(display)
        if tx == tx_id:
            return str(display), tuple(str(c) for c in info.get("dtcs") or [])
    if entry.get("scope") != ALL_SCOPE:
        return None
    if any(display_tx(str(u)) == tx_id for u in entry.get("unread") or []):
        return None
    return "", ()


def ecu_status(
    tx_id: int,
    name: str | None = None,
    *,
    car: Car | None = None,
    data: Mapping[str, Any] | None = None,
) -> EcuStatus | None:
    """The ECU's codes at its latest admitted scan, or None when no scan read it.

    ``name`` is the ECU's registry name, for the revision check (defaults to the name
    the scan recorded)."""
    car = car or current_car()
    scans = (data if data is not None else load_log()).get("scans") or []
    for entry in reversed(scans):
        if not isinstance(entry, Mapping):
            continue
        key = scope_key(str(entry.get("scope", "")))
        if key not in (ALL_SCOPE, tx_id):
            continue
        read = _read_in(entry, tx_id)
        if read is None:
            continue
        display, codes = read
        ecu_name = name or (display_name(display) if display else None)
        if not car.admits(entry, [ecu_name] if ecu_name else []):
            continue
        return EcuStatus(
            tx=tx_id,
            ecu=display or (f"{name} (0x{tx_id:03X})" if name else f"0x{tx_id:03X}"),
            codes=codes,
            timestamp=str(entry.get("timestamp", "?")),
            label=entry.get("label"),
        )
    return None


def logged_ecus(data: Mapping[str, Any]) -> dict[int, str]:
    """TX id -> display label of every ECU any scan recorded codes or a reading for."""
    out: dict[int, str] = {}
    for entry in data.get("scans") or []:
        for display, info in ((entry or {}).get("ecus") or {}).items():
            tx = display_tx(str((info or {}).get("tx") or "")) or display_tx(display)
            if tx is not None:
                out[tx] = str(display)
    return out


def open_dtcs(*, car: Car | None = None, data: Mapping[str, Any] | None = None) -> list[EcuStatus]:
    """Every ECU whose latest admitted scan read codes, by TX id."""
    car = car or current_car()
    data = data if data is not None else load_log()
    out = []
    for tx, display in sorted(logged_ecus(data).items()):
        status = ecu_status(tx, display_name(display), car=car, data=data)
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
    seen = set()
    for entry in admitted:
        for display, info in (entry.get("ecus") or {}).items():
            tx = display_tx(str((info or {}).get("tx") or "")) or display_tx(display)
            for code in (info or {}).get("dtcs") or []:
                seen.add((tx if tx is not None else display, str(code)))
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
