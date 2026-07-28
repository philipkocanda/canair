"""Transport diagnostics — per-exchange outcome counting (drops/errors/decode).

Every request client (:class:`~canlib.terminal.WiCANTerminal`,
:class:`~canlib.transport.raw_terminal.RawTerminal`,
:class:`~canlib.transport.uds_raw.RawUdsClient`) already keeps a
:class:`~canlib.timing.TimingRecorder` (``.timings``); this is the sibling that
counts *outcomes* rather than *latency*. Each completed UDS exchange is
classified (:func:`canlib.uds_parse.classify_response`) into an outcome bucket
and tallied, so:

- the live monitor can surface **dropped frames** and error rates in its status
  line (raising awareness of connection/latency problems), and
- a recorded ``--save`` session can carry a small **data-quality** footprint
  (which transport, how many exchanges, how many drops/errors) — the provenance
  that makes historical captures trustworthy after the ISO-TP reassembly bugs.

Recording is a cheap dict update on the hot path; error-category outcomes are
additionally emitted to the central rotating event log
(:func:`canlib.log.log_event`) so the raw exchange is inspectable after the fact
(``canair logs``).
"""

from __future__ import annotations

from .uds_parse import (
    ERROR_CATEGORIES,
    RESPONSE_CATEGORIES,
    UdsResponse,
    classify_response,
)


def classify_raw_value(value) -> str:
    """Classify a raw poll result (``bytes`` / ``Exception`` / ``None``).

    The pipelined raw-CAN client (:class:`~canlib.transport.uds_raw.RawUdsClient`)
    returns reassembled bytes, an ``Exception`` (per-request failure), or
    ``None``/``TimeoutError`` (no answer) — it does not go through
    :func:`parse_uds_response`. Map those to the same taxonomy so raw-path drops
    and timeouts count alongside the ELM path's.
    """
    if value is None or isinstance(value, TimeoutError):
        return "no_data"
    if isinstance(value, Exception):
        return "bus"
    data = bytes(value)
    if not data:
        return "no_data"
    if data[0] == 0x7F:
        return "nrc"
    return "ok"


class TransportStats:
    """Counts per-exchange outcomes for one request client.

    Attached to each client as ``.diag``. ``transport`` is the resolved
    transport label (e.g. ``"slcan-tcp"`` / ``"wican-ws"``) carried into the
    central event log and recorded-capture provenance. Recording is a no-op-cheap
    dict update; snapshotting/serialising happens off the hot path.
    """

    def __init__(self, transport: str | None = None) -> None:
        self.transport = transport
        self.counts: dict[str, int] = dict.fromkeys(RESPONSE_CATEGORIES, 0)

    # -- recording ---------------------------------------------------------
    def record(
        self,
        category: str,
        *,
        ecu: str | None = None,
        pid: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Tally one exchange outcome; log the raw event for error categories."""
        if category not in self.counts:
            category = "other"
        self.counts[category] += 1
        if category in ERROR_CATEGORIES:
            from .log import log_event

            log_event(
                category,
                detail or category,
                transport=self.transport,
                ecu=ecu,
                pid=pid,
            )

    def record_response(
        self, resp: UdsResponse, *, ecu: str | None = None, pid: str | None = None
    ) -> str:
        """Classify + record a parsed :class:`UdsResponse`. Returns the category."""
        category = classify_response(resp)
        self.record(category, ecu=ecu, pid=pid, detail=resp.get("error"))
        return category

    def record_raw(self, value, *, ecu: str | None = None, pid: str | None = None) -> str:
        """Classify + record a raw ``bytes``/``Exception``/``None`` result."""
        category = classify_raw_value(value)
        detail = str(value) if isinstance(value, Exception) else None
        self.record(category, ecu=ecu, pid=pid, detail=detail)
        return category

    # -- reads -------------------------------------------------------------
    @property
    def exchanges(self) -> int:
        """Total exchanges recorded (every category)."""
        return sum(self.counts.values())

    @property
    def drops(self) -> int:
        """ISO-TP reassembly failures (drop + stale) — the headline signal."""
        return self.counts["drop"] + self.counts["stale"]

    @property
    def errors(self) -> int:
        """All data-quality problems (everything that isn't ``ok``/``nrc``)."""
        return sum(self.counts[c] for c in ERROR_CATEGORIES)

    def __bool__(self) -> bool:
        return self.exchanges > 0

    def snapshot(self) -> dict[str, int]:
        """A copy of the raw per-category counts."""
        return dict(self.counts)

    def quality(self) -> dict:
        """Compact data-quality summary for capture metadata.

        Always carries ``exchanges``; error categories are included only when
        non-zero so a clean session's record stays terse. ``nrc`` is a
        legitimate ECU answer, not a fault, so it is not reported here.
        """
        q: dict = {"exchanges": self.exchanges}
        for cat in ERROR_CATEGORIES:
            n = self.counts[cat]
            if n:
                q[cat] = n
        return q

    def diff(self, base: dict[str, int]) -> TransportStats:
        """A new recorder holding the delta of ``self`` since a ``snapshot()`` ``base``.

        Used by the monitor to attribute per-segment quality: snapshot at segment
        start, diff at reconcile. Never negative (a fresh base yields ``self``).
        """
        delta = TransportStats(self.transport)
        for cat in RESPONSE_CATEGORIES:
            delta.counts[cat] = max(0, self.counts[cat] - base.get(cat, 0))
        return delta
