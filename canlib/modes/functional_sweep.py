"""Turn the replies to one functional request into a routing map (pure, no I/O).

``canair discover --functional`` broadcasts one read and hears every ECU the
gateway routes it to. This module decides what each reply means: which
registered ECU sent it (by its response id), on which bus that ECU sits, whether
it answered positively or with an NRC, which known ECUs stayed silent, and which
messages did not answer the request at all (an in-car tester's own traffic falls
in the same id block). Grouped by bus, the responders are the routing map.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from canlib.addressing import AddressingMode, physical_request_id
from canlib.captures.types import CaptureRecord, RespondingEntry, ScanResults
from canlib.safety import session_protocol
from canlib.transport.functional import FunctionalResult, is_pending
from canlib.uds.parse import parse_uds_response, request_echo

UNKNOWN_BUS = "(no bus declared)"
NOT_IN_PROFILE = "not in profile"


@dataclass(frozen=True)
class Responder:
    """One ECU's answer. ``tx_id`` is its physical request id (where flow control went)."""

    rx_id: int
    tx_id: int
    name: str | None  # None: the profile does not know this response id
    can_bus: tuple[str, ...]
    status: str  # "positive" or "NRC 0xNN"
    detail: str  # the NRC's meaning, empty for a positive reply
    payload_hex: str
    elapsed_ms: int | None
    pending: int = 0  # ResponsePending replies before the final one

    @property
    def known(self) -> bool:
        return self.name is not None

    @property
    def bus_key(self) -> str:
        if not self.known:
            return NOT_IN_PROFILE
        return "/".join(self.can_bus) or UNKNOWN_BUS


@dataclass(frozen=True)
class SilentEcu:
    """A registered ECU that did not answer."""

    name: str
    tx_id: int
    rx_id: int
    can_bus: tuple[str, ...]

    @property
    def bus_key(self) -> str:
        return "/".join(self.can_bus) or UNKNOWN_BUS


@dataclass(frozen=True)
class OtherMessage:
    """A message heard during the window that does not answer the request."""

    rx_id: int
    payload_hex: str


@dataclass
class SweepRound:
    """Everything one functional request showed."""

    index: int
    time: str
    responders: list[Responder] = field(default_factory=list)
    silent: list[SilentEcu] = field(default_factory=list)
    other: list[OtherMessage] = field(default_factory=list)
    lost: int = 0


def _buses(entry: Mapping[str, Any]) -> tuple[str, ...]:
    value = entry.get("can_bus") or ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(b) for b in value)


def classify_round(
    result: FunctionalResult,
    ecus: Mapping[int, Mapping[str, Any]],
    *,
    index: int = 1,
    time: str = "",
    rx_offset: int = 0x08,
    mode: AddressingMode = AddressingMode.NORMAL_11BIT,
    hk_f1xx_offset: bool = False,
) -> SweepRound:
    """Classify every message in ``result`` against the ECU registry ``ecus``.

    ``ecus`` is :func:`canlib.ecus.load_ecus` output (keyed by request id, each
    entry with its resolved ``rx_id``/``address``/``can_bus``). A responder's
    last reply counts; ResponsePending replies before it are counted, not shown.
    """
    request = result.request
    req_hex = request.hex().upper()
    echo = request_echo(req_hex)
    expected_echo = echo[1] if echo else None
    by_rx = {int(entry["rx_id"]): (tx, entry) for tx, entry in ecus.items()}
    by_tx = {tx: entry["address"] for tx, entry in ecus.items() if "address" in entry}

    sweep = SweepRound(index=index, time=time, lost=result.lost)
    pending: dict[int, int] = {}
    final: dict[int, tuple[str, int | None, int | None, str]] = {}
    for message in result.messages:
        if is_pending(message.payload, request[0]):
            pending[message.rx_id] = pending.get(message.rx_id, 0) + 1
            continue
        known = by_rx.get(message.rx_id)
        payload_hex = message.payload.hex().upper()
        resp = parse_uds_response(
            payload_hex,
            expected_sid=request[0],
            expected_echo=expected_echo,
            hk_f1xx_offset=hk_f1xx_offset,
            protocol=session_protocol(known[1].get("id_protocol")) if known else None,
        )
        nrc = resp.get("nrc")
        if not resp.get("ok") and nrc is None:
            sweep.other.append(OtherMessage(message.rx_id, payload_hex))
            continue
        final[message.rx_id] = (
            payload_hex,
            message.elapsed_ms,
            nrc,
            str(resp.get("nrc_desc", "")),
        )

    for rx_id, (payload_hex, elapsed_ms, nrc, nrc_desc) in final.items():
        known = by_rx.get(rx_id)
        if known is not None:
            tx_id, entry = known
            name, buses = str(entry["name"]), _buses(entry)
        else:
            tx_id = physical_request_id(rx_id, by_tx, rx_offset, mode)
            name, buses = None, ()
        sweep.responders.append(
            Responder(
                rx_id=rx_id,
                tx_id=tx_id,
                name=name,
                can_bus=buses,
                status="positive" if nrc is None else f"NRC 0x{nrc:02X}",
                detail="" if nrc is None else nrc_desc,
                payload_hex=payload_hex,
                elapsed_ms=elapsed_ms,
                pending=pending.get(rx_id, 0),
            )
        )

    answered = {r.rx_id for r in sweep.responders}
    for rx_id, (tx_id, entry) in sorted(by_rx.items(), key=lambda kv: str(kv[1][1]["name"])):
        if rx_id not in answered:
            sweep.silent.append(SilentEcu(str(entry["name"]), tx_id, rx_id, _buses(entry)))
    return sweep


def group_by_bus(items: Sequence[Responder]) -> dict[str, list[Responder]]:
    """Responders by bus, known buses first (alphabetically), then unknown responders."""
    groups: dict[str, list[Responder]] = {}
    for item in sorted(items, key=lambda r: (not r.known, r.bus_key, r.rx_id)):
        groups.setdefault(item.bus_key, []).append(item)
    return groups


def silent_by_bus(items: Sequence[SilentEcu]) -> dict[str, list[SilentEcu]]:
    groups: dict[str, list[SilentEcu]] = {}
    for item in sorted(items, key=lambda s: (s.bus_key, s.name)):
        groups.setdefault(item.bus_key, []).append(item)
    return groups


def hex_id(can_id: int) -> str:
    """A CAN id as the captures and reports write it: 3 hex digits, or 8 when 29-bit."""
    return f"0x{can_id:08X}" if can_id > 0x7FF else f"0x{can_id:03X}"


def round_to_json(sweep: SweepRound) -> dict[str, Any]:
    """The ``--json`` form of one round."""
    return {
        "round": sweep.index,
        "time": sweep.time,
        "responders": [
            {
                "rx": hex_id(r.rx_id),
                "tx": hex_id(r.tx_id),
                "name": r.name,
                "known": r.known,
                "can_bus": list(r.can_bus),
                "status": r.status,
                "detail": r.detail,
                "payload": r.payload_hex,
                "elapsed_ms": r.elapsed_ms,
                "pending": r.pending,
            }
            for r in sweep.responders
        ],
        "silent": [
            {
                "name": s.name,
                "tx": hex_id(s.tx_id),
                "rx": hex_id(s.rx_id),
                "can_bus": list(s.can_bus),
            }
            for s in sweep.silent
        ],
        "other": [{"rx": hex_id(o.rx_id), "payload": o.payload_hex} for o in sweep.other],
        "lost": sweep.lost,
    }


def capture_pid(functional_id: int, request_hex: str) -> str:
    """The ``pid`` a saved sweep is filed under (a ``discover …`` capture)."""
    return f"discover functional {functional_id:X} {request_hex.upper()}"


def round_to_capture(sweep: SweepRound, functional_id: int, request_hex: str) -> CaptureRecord:
    """One round as a discover-style capture: ``rx: broadcast``, one entry per responder.

    Each responder's ``tx`` is its physical request id (the request itself went to
    the functional id, which the ``pid`` names), so an entry reads like one from a
    physical ``discover`` sweep of that ECU.
    """
    responding: list[RespondingEntry] = []
    for r in sweep.responders:
        responding.append(
            {
                "tx": hex_id(r.tx_id),
                "rx": hex_id(r.rx_id),
                "response": f"{r.name} ({r.status})" if r.name else r.status,
                "notes": f"Raw: {r.payload_hex}",
            }
        )
    results: ScanResults = {}
    if responding:
        results["responding"] = responding
    if sweep.silent:
        results["rejected"] = f"{len(sweep.silent)} known ECUs silent"
        results["notes"] = "Silent: " + ", ".join(s.name for s in sweep.silent)
    capture: CaptureRecord = {"rx": "broadcast", "pid": capture_pid(functional_id, request_hex)}
    if sweep.time:
        capture["time"] = sweep.time
    capture["scan_results"] = results
    return capture
