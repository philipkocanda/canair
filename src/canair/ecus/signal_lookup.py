"""Finding a diagnostic signal by name across every ECU and PID of a profile.

A signal name is unique only among the signals that ship to the device (``canair validate pids``).
The same name may still be defined on several PIDs, for example one quantity read through two
services. A lookup therefore returns every definition, and the caller decides what to do when
there is more than one.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from .pids import EcuIndexEntry


@dataclass(frozen=True)
class SignalRef:
    """One signal definition: where it lives and what it decodes to."""

    ecu: str
    pid: str
    name: str
    unit: str
    verified: bool

    @property
    def selector(self) -> str:
        """The ``ECU:PID:NAME`` form that names this definition and no other."""
        return f"{self.ecu}:{self.pid}:{self.name}"


def parse_signal_ref(text: str) -> tuple[str | None, str | None, str]:
    """Split ``NAME``, ``ECU:NAME`` or ``ECU:PID:NAME`` into ``(ecu, pid, name)``."""
    parts = [p.strip() for p in text.split(":")]
    if len(parts) > 3 or not all(parts):
        raise ValueError(f"invalid signal {text!r} (expected NAME, ECU:NAME or ECU:PID:NAME)")
    *scope, name = parts
    ecu = scope[0] if scope else None
    pid = scope[1] if len(scope) > 1 else None
    return ecu, pid, name


def _signals(index: dict[str, EcuIndexEntry]):
    for ecu, entry in sorted(index.items()):
        for pid, pid_entry in sorted(entry["pids"].items()):
            for name, definition in (pid_entry.get("parameters") or {}).items():
                yield ecu, pid, str(name), definition if isinstance(definition, dict) else {}


def find_signals(
    index: dict[str, EcuIndexEntry], name: str, *, ecu: str | None = None, pid: str | None = None
) -> list[SignalRef]:
    """Every definition called ``name`` (case-insensitive), narrowed to ``ecu`` and ``pid``."""
    return [
        SignalRef(e, p, n, str(d.get("unit") or ""), d.get("verified", False) is True)
        for e, p, n, d in _signals(index)
        if n.upper() == name.upper()
        and (ecu is None or e == ecu.upper())
        and (pid is None or p == pid.upper())
    ]


def similar_signal_names(index: dict[str, EcuIndexEntry], name: str, limit: int = 5) -> list[str]:
    """Defined names that contain ``name``, else the closest spellings."""
    names = sorted({n for _, _, n, _ in _signals(index)})
    by_upper = {n.upper(): n for n in names}
    needle = name.upper()
    hits = [n for n in names if needle in n.upper()]
    if not hits:
        hits = [by_upper[u] for u in difflib.get_close_matches(needle, by_upper, n=limit)]
    return hits[:limit]
