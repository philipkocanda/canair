"""Per-(ECU, PID) round-trip timing — lightweight request instrumentation.

Every request client (:class:`~canlib.terminal.WiCANTerminal`,
:class:`~canlib.transport.raw_terminal.RawTerminal`,
:class:`~canlib.transport.uds_raw.RawUdsClient`) already measures per-command
elapsed time; this keeps a small per-``(ecu, pid)`` aggregate so slow PIDs/ECUs
can be surfaced (``canair query --timings``) and used to validate timeout /
pipeline changes. Purely additive and cheap — a dict update per request, no
effect on the hot path.

ECU labels are stored as the client naturally has them: the raw/ELM clients pass
the request TX id as ``"0x7E4"`` (zero-cost, no registry lookup on the hot path),
and :func:`render_timings` resolves those to names once, at print time.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

_HEX_LABEL = re.compile(r"^0x[0-9A-Fa-f]+$")


@dataclass
class Stat:
    """Running aggregate for one ``(ecu, pid)`` key."""

    n: int = 0
    total: float = 0.0
    max: float = 0.0
    last: float = 0.0

    def add(self, elapsed: float) -> None:
        self.n += 1
        self.total += elapsed
        if elapsed > self.max:
            self.max = elapsed
        self.last = elapsed

    @property
    def mean(self) -> float:
        return self.total / self.n if self.n else 0.0


class TimingRecorder:
    """Collects per-``(ecu, pid)`` round-trip times.

    Attached to each request client as ``.timings``. Recording is a no-op-cheap
    dict update; rendering/snapshotting happens once at the end of a run.
    """

    def __init__(self) -> None:
        self._stats: dict[tuple[str, str], Stat] = {}

    def record(self, ecu: str, pid: str, elapsed: float) -> None:
        key = (ecu, pid)
        st = self._stats.get(key)
        if st is None:
            st = self._stats[key] = Stat()
        st.add(elapsed)

    def __bool__(self) -> bool:
        return bool(self._stats)

    def snapshot(self) -> list[dict]:
        """Rows sorted slowest-first (by max RTT), times in milliseconds."""
        rows = [
            {
                "ecu": ecu,
                "pid": pid,
                "n": st.n,
                "mean_ms": round(st.mean * 1000.0, 1),
                "max_ms": round(st.max * 1000.0, 1),
                "last_ms": round(st.last * 1000.0, 1),
            }
            for (ecu, pid), st in self._stats.items()
        ]
        rows.sort(key=lambda r: r["max_ms"], reverse=True)
        return rows


def _resolve_ecu(label: str) -> str:
    """Turn a ``"0x7E4"`` request-id label into an ECU name when known."""
    if not _HEX_LABEL.match(label):
        return label
    try:
        from .ecus import ecu_name

        return ecu_name(int(label, 16))
    except Exception:
        return label


def render_timings(recorder: TimingRecorder):
    """Build a Rich table of the slowest PIDs (or None if nothing recorded)."""
    rows = recorder.snapshot()
    if not rows:
        return None
    from rich.table import Table

    table = Table(title="Response timing (slowest first)", title_style="bold")
    table.add_column("ECU")
    table.add_column("PID")
    table.add_column("n", justify="right")
    table.add_column("mean ms", justify="right")
    table.add_column("max ms", justify="right")
    table.add_column("last ms", justify="right")
    for r in rows:
        table.add_row(
            _resolve_ecu(r["ecu"]),
            r["pid"],
            str(r["n"]),
            f"{r['mean_ms']:.1f}",
            f"{r['max_ms']:.1f}",
            f"{r['last_ms']:.1f}",
        )
    return table


def print_timings(recorder: TimingRecorder | None, as_json: bool = False, stream=None) -> None:
    """Print timing stats to ``stream`` (default stderr, so stdout/JSON stays clean).

    In ``--json`` mode the snapshot is emitted as a JSON object on stderr; the
    command's real JSON result stays uncorrupted on stdout.
    """
    if not recorder:
        return
    stream = stream if stream is not None else sys.stderr
    if as_json:
        import json

        # Resolve labels so JSON consumers get ECU names too.
        rows = [{**r, "ecu": _resolve_ecu(r["ecu"])} for r in recorder.snapshot()]
        print(json.dumps({"timings": rows}), file=stream)
        return
    table = render_timings(recorder)
    if table is None:
        return
    from rich.console import Console

    Console(file=stream).print(table)
