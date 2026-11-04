"""The shape of one decoded poll result, shared by every reader and writer of one.

A poll yields one :class:`ResultEntry` per PID; a frame groups them per ECU. The live monitor
renderer, the compact printer, the capture collectors and state auto-suggestion all read this
shape, so it sits beside the decoder that fills ``params`` rather than in any one of them.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from canair.expression.decoding import ParamRow


class ResultEntry(TypedDict):
    """One PID's shaped poll result, as emitted by the multi/monitor pipeline.

    The single dict shape shared across the live monitor renderer
    (:mod:`canair.live._monitor_render`), the compact printer
    (:func:`canair.render.formatting.print_ecu_results`), the capture collectors
    (:mod:`canair.live.multi`), and state auto-suggestion
    (:func:`canair.states.collect_values`). ``pid`` is always present; the rest
    depend on the outcome — a successful decode carries ``params``/``raw_hex``,
    an unmapped PID adds ``decode``/``unmapped``, a failed query carries
    ``error``, and the monitor tags a timed-out-but-retained row ``stale``.
    """

    pid: str
    params: NotRequired[list[ParamRow]]
    raw_hex: NotRequired[str]
    acquired_at: NotRequired[float | None]
    elapsed_ms: NotRequired[int]
    # Seconds the request behind this entry held the pipe, keepalives excluded (a batch's
    # members share their one request's). Feeds the monitor's backoff; never recorded.
    request_s: NotRequired[float]
    decode: NotRequired[str | None]
    unmapped: NotRequired[bool]
    error: NotRequired[str]
    # The negative response code when ``error`` is a negative response.
    nrc: NotRequired[int]
    stale: NotRequired[bool]
    sensitive: NotRequired[bool]
    # Payload indices of the PID's declared support-map bytes (display only; never recorded).
    map_bytes: NotRequired[tuple[int, ...]]


# One render/collect frame: per-ECU ``(label, results)`` pairs, where ``label``
# is e.g. ``"BMS (0x7E4)"`` and ``results`` is that ECU's PID entries in order.
EcuFrame = tuple[str, list[ResultEntry]]
