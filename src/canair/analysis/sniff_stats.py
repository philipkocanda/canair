"""Per-arbitration-ID statistics of sniffed CAN frames (``canair sniff``).

The sniffer's python-can listener feeds :class:`SniffStats` from a background thread; the table
(:func:`canair.render.sniff.render_sniff_table`) and the Textual view read snapshots of it.
"""

from __future__ import annotations

import threading


class SniffStats:
    """Thread-safe per-arbitration-ID aggregation of sniffed CAN frames."""

    def __init__(self):
        self._by_id: dict[int, dict] = {}
        self._lock = threading.Lock()

    def record(self, arb_id: int, data: bytes, ts: float, extended: bool = False) -> None:
        with self._lock:
            e = self._by_id.get(arb_id)
            if e is None:
                self._by_id[arb_id] = {
                    "count": 1,
                    "first": ts,
                    "last": ts,
                    "data": bytes(data),
                    "changed": bytearray(len(data)),
                    "extended": extended,
                }
                return
            e["count"] += 1
            old = e["data"]
            if len(old) == len(data):
                changed = e["changed"]
                for i, (a, b) in enumerate(zip(old, data, strict=False)):
                    if a != b:
                        changed[i] = 1
            else:
                # DLC changed — resize the mask, mark everything volatile.
                e["changed"] = bytearray([1] * len(data))
            e["data"] = bytes(data)
            e["last"] = ts

    def clear(self) -> None:
        with self._lock:
            self._by_id.clear()

    def snapshot(self) -> list[dict]:
        """Return per-ID rows (sorted by ID) with a computed rate in Hz."""
        rows = []
        with self._lock:
            for arb_id, e in sorted(self._by_id.items()):
                span = e["last"] - e["first"]
                hz = (e["count"] - 1) / span if e["count"] > 1 and span > 0 else 0.0
                rows.append(
                    {
                        "id": arb_id,
                        "extended": e["extended"],
                        "count": e["count"],
                        "hz": hz,
                        "data": e["data"],
                        "changed": bytes(e["changed"]),
                    }
                )
        return rows

    @property
    def total_frames(self) -> int:
        with self._lock:
            return sum(e["count"] for e in self._by_id.values())
