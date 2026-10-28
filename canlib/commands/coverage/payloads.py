"""Pick, per PID, the captured reply that sets its length for ``canair coverage``.

Selection rules (each one a regression that once misreported a gap):

- **Newest session wins**, not the longest-ever payload. Before 2026-07-28 the final
  CAN frame of a multi-frame response was stored padded to 8 bytes, and that padding
  is invisible in the stored hex (the ISO-TP length is gone), so the padded length
  always won and its filler was reported as undecoded data. Sessions are ordered by
  ``(date, first capture time, file, index)`` — the same date-then-time order as the
  capture merge driver — because file order is not chronological once
  ``canair contribute`` has appended sessions to an upstream file. An undated session
  sorts oldest.
- Within that session the **most common length** wins (longer on a tie), so a one-off
  stray read does not set it.
- A ``variable_length:`` PID has no single right length, so it takes its **longest**
  reply in scope (preferring one that cannot be padded); a short variant must not
  hide signals that read the longer one.
- Only a positive reply that **echoes the PID** counts — an NRC (``7F …``) has no data
  bytes and a misfiled reply belongs to another PID. A PID that only ever answered
  with an NRC is reported as such, not as "no capture".
"""

from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TypedDict

from canlib.captures import IMPORT_TRANSPORT
from canlib.uds.parse import NRC_CODES, payload_echo_mismatch, request_echo

PidKey = tuple[str, str]

# Bytes ECUs commonly pad an ISO-TP frame with. Old captures kept that filler, so a
# payload ending in anything else cannot have a padded tail.
_FILL_BYTES = frozenset({"00", "55", "AA", "CC", "FF"})


class RepresentativePayload(TypedDict):
    """The positive reply that sets one PID's length."""

    payload: str
    date: str
    label: str
    file: str
    may_include_padding: bool


class NegativeOnly(TypedDict):
    """A PID that, in scope, only ever answered with a negative response."""

    nrc: str
    name: str
    date: str
    file: str
    count: int


@dataclass(frozen=True)
class CoverageCaptures:
    """Everything ``coverage`` needs from the captures, keyed by ``(ECU, PID)``."""

    payloads: dict[PidKey, RepresentativePayload] = field(default_factory=dict)
    negative_only: dict[PidKey, NegativeOnly] = field(default_factory=dict)


def is_frame_aligned(length: int) -> bool:
    """True if ``length`` is exactly a whole number of CAN frames (``6 + 7k`` bytes).

    A multi-frame payload of this length is indistinguishable from a shorter one whose
    final frame was padded out to 8 bytes.
    """
    return length > 7 and (length - 6) % 7 == 0


def may_be_padded(
    payloads: Collection[str], recorded_version: str | None, transport: str | None = None
) -> bool:
    """True if a length shared by ``payloads`` may include trailing frame padding.

    Two kinds of session can hold a padded final frame: one recorded before sessions
    stamped a ``version`` (the old capture path stored it padded), and a ``canair
    import uds`` session — a pasted reading of unknown provenance, which carries a
    version but no guarantee its source stripped the padding. Within those, only a
    frame-aligned length whose **every** payload ends in the **same** fill byte can
    hide padding: an ECU pads with one filler, so differing last bytes are data.
    """
    if recorded_version and transport != IMPORT_TRANSPORT:
        return False
    tails = {p[-2:].upper() for p in payloads}
    if len(tails) != 1 or not tails <= _FILL_BYTES:
        return False
    return all(is_frame_aligned(len(p) // 2) for p in payloads)


def session_length(lengths: Counter) -> int:
    """The payload length one session reports: its most common, longer on a tie."""
    return max(lengths, key=lambda n: (lengths[n], n))


def _nrc_code(pid: str, payload: str) -> int | None:
    """The NRC byte of a ``7F <SID> <NRC>`` reply to ``pid``, else None.

    A negative reply echoes the request SID, so one naming another service is a
    misfiled frame rather than this PID's answer.
    """
    if len(payload) < 6 or payload[:2].upper() != "7F":
        return None
    try:
        sid, code = int(payload[2:4], 16), int(payload[4:6], 16)
    except ValueError:
        return None
    echo = request_echo(pid)
    if echo is not None and echo[0] != sid:
        return None
    return code


@dataclass
class _Session:
    order: tuple[str, str, str, int]
    meta: Mapping
    # pid -> length -> every payload of that length
    by_length: dict[PidKey, dict[int, list[str]]] = field(default_factory=dict)

    def padded(self, payloads: Collection[str]) -> bool:
        return may_be_padded(payloads, self.meta.get("session_version"), self.meta.get("transport"))

    def pick(self, key: PidKey, n: int) -> RepresentativePayload:
        payloads = self.by_length[key][n]
        return {
            "payload": payloads[0],
            "date": str(self.meta.get("date") or ""),
            "label": self.meta.get("session_label", ""),
            "file": self.meta.get("file", ""),
            "may_include_padding": self.padded(payloads),
        }


def select_captures(
    entries: Iterable[Mapping],
    *,
    variable_length: Collection[PidKey] = frozenset(),
    hk_f1xx_offset: bool = False,
) -> CoverageCaptures:
    """Choose each PID's representative reply from flattened capture rows.

    ``entries`` are :func:`canlib.captures.store.load_all_captures` rows, in any order.
    ``variable_length`` names the ``(ECU, PID)`` keys flagged ``variable_length:``;
    ``hk_f1xx_offset`` tolerates the Hyundai/Kia identity-DID echo quirk.
    """
    sessions: dict[tuple, _Session] = {}
    first_time: dict[tuple, str] = {}
    nrcs: dict[PidKey, list[tuple[tuple, int]]] = {}
    for e in entries:
        path = str(e.get("_path") or e.get("file") or "")
        skey = (path, int(e.get("_session_idx") or 0))
        t = str(e.get("time") or "")
        if t and (skey not in first_time or t < first_time[skey]):
            first_time[skey] = t
        sess = sessions.setdefault(skey, _Session(order=("", "", path, skey[1]), meta=e))
        payload = (e.get("payload") or "").replace(" ", "")
        if not payload:
            continue
        pid = str(e.get("pid", "")).upper()
        pkey = (str(e.get("ecu", "")).upper(), pid)
        if payload[:2].upper() == "7F":
            code = _nrc_code(pid, payload)
            if code is not None:
                nrcs.setdefault(pkey, []).append((skey, code))
            continue
        if payload_echo_mismatch(pid, payload, hk_f1xx_offset):
            continue
        sess.by_length.setdefault(pkey, {}).setdefault(len(payload) // 2, []).append(payload)

    for skey, sess in sessions.items():
        sess.order = (str(sess.meta.get("date") or ""), first_time.get(skey, ""), *skey)
    ordered = sorted(sessions.values(), key=lambda s: s.order)

    out = CoverageCaptures()
    for sess in ordered:  # oldest first, so a newer session overwrites
        for pkey, lengths in sess.by_length.items():
            if pkey not in variable_length:
                counts = Counter({n: len(ps) for n, ps in lengths.items()})
                out.payloads[pkey] = sess.pick(pkey, session_length(counts))
    for pkey in variable_length:
        candidates = [
            (not s.padded(ps), n, s.order, s)
            for s in ordered
            for n, ps in s.by_length.get(pkey, {}).items()
        ]
        if candidates:
            _trusted, n, _order, sess = max(candidates, key=lambda c: c[:3])
            out.payloads[pkey] = sess.pick(pkey, n)

    for pkey, seen in nrcs.items():
        if pkey in out.payloads:
            continue
        skey, code = max(seen, key=lambda sc: sessions[sc[0]].order)
        meta = sessions[skey].meta
        out.negative_only[pkey] = {
            "nrc": f"0x{code:02X}",
            "name": NRC_CODES.get(code, "unknown"),
            "date": str(meta.get("date") or ""),
            "file": meta.get("file", ""),
            "count": len(seen),
        }
    return out


def representative_payloads(
    entries: Iterable[Mapping], **kwargs
) -> dict[PidKey, RepresentativePayload]:
    """The positive reply that sets each PID's length (see :func:`select_captures`)."""
    return select_captures(entries, **kwargs).payloads


def load_coverage_captures(
    since=None,
    until=None,
    state=None,
    label=None,
    *,
    variable_length: Collection[PidKey] = frozenset(),
    hk_f1xx_offset: bool = False,
) -> CoverageCaptures:
    """:func:`select_captures` over the active profile's captures, in scope."""
    from canlib.captures.dates import filter_by_date_range, filter_by_text
    from canlib.captures.store import load_all_captures

    entries = filter_by_date_range(load_all_captures(), since, until)
    return select_captures(
        filter_by_text(entries, state=state, label=label),
        variable_length=variable_length,
        hk_f1xx_offset=hk_f1xx_offset,
    )


def load_representative_payloads(
    since=None, until=None, state=None, label=None, **kwargs
) -> dict[PidKey, RepresentativePayload]:
    """:func:`representative_payloads` over the active profile's captures, in scope."""
    return load_coverage_captures(since, until, state, label, **kwargs).payloads
