"""Turn raw string hits into *findings* — one row per distinct location, not per capture.

A search over a capture store matches the same string thousands of times: ``EHEL-MS2``
sits at one offset of one PID and is in 95 captures. The report unit is therefore the
**location** (ECU, PID, offset, string), carrying a hit count and a first/last-seen
span. Grouping collapses thousands of raw ``strings(1)`` hits into a couple of dozen
rows on the bundled Ioniq profile.

Pure functions over small row records; loading captures and rendering live in
:mod:`.haystack` and :mod:`.render`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from canlib.byteindex import elm_to_wican_idx
from canlib.textscan import Span, ascii_fold, folded_runs, is_plausible_token
from canlib.uds_layout import response_layout

# A single-frame response (<= 7 bytes) carries one PCI byte, a multi-frame one two.
# Both the WiCAN expression and the run's byte labels depend on which, so a group
# that mixes the two layouts has no one correct expression.
_SINGLE_FRAME_MAX = 7

SNIPPET_CONTEXT = 24

KIND_TEXT = "text"
KIND_BYTES = "bytes"

FIELD_PAYLOAD = "payload"


@dataclass(frozen=True)
class PayloadRow:
    """One distinct ``(ecu, pid, payload)`` and how often / when it was captured.

    ``field`` names where the bytes came from: a capture's ``payload``, or bytes
    recovered from a scan note's ``Raw:`` hex (``lossy`` when that hex was cut short).
    """

    ecu: str
    pid: str
    data: bytes
    weight: int
    first: str
    last: str
    field: str = FIELD_PAYLOAD
    lossy: bool = False


@dataclass(frozen=True)
class TextRow:
    """One distinct free-text value (a label, a note, a text response, …)."""

    field: str
    ecu: str
    pid: str
    text: str
    weight: int
    first: str
    last: str


@dataclass(frozen=True)
class Finding:
    """One distinct location a string was found.

    ``offset``/``length`` are an ISO-TP payload index and byte count (payload hits
    only); ``expression`` is the paste-ready WiCAN ``[Bn:Bm]`` range for that run, or
    ``None`` when no single expression is correct for every capture in the group.
    ``mixed_frames`` marks a group whose captures are single- *and* multi-frame, so a
    WiCAN byte label would be right for only some of them.
    """

    source: str  # "payload" | "text" | "defs"
    field: str
    ecu: str
    pid: str
    text: str
    hits: int
    first: str
    last: str
    match: str | None = None
    offset: int | None = None
    length: int = 0
    payload_len: int = 0
    variants: int = 1
    expression: str | None = None
    lossy: bool = False
    mixed_frames: bool = False


@dataclass(frozen=True)
class _Run:
    """A printable (or matched) byte run found in one distinct payload."""

    start: int
    end: int
    text: str
    row: PayloadRow
    match: str = ""


TextSpans = Callable[[str], list[Span]]
ByteSpans = Callable[[bytes], list[Span]]
MatchKey = Callable[[str], str]


def wican_expression(offset: int, length: int, payload_len: int) -> str | None:
    """The ``[Bn:Bm]`` WiCAN range for ``length`` data bytes starting at ``offset``.

    ``offset`` is an ISO-TP payload index. The expression may span interleaved PCI
    bytes (so it looks non-contiguous); that is correct, because the byte-run types
    (``ascii``/``date``) drop exactly those framing bytes when they decode it. A
    one-byte run has no range form the ascii decoder accepts, so it gets none.
    """
    if length < 2:
        return None
    first = elm_to_wican_idx(offset, payload_len)
    last = elm_to_wican_idx(offset + length - 1, payload_len)
    return f"[B{first}:B{last}]"


def data_start(data: bytes) -> int:
    """Index of the first data byte: past the response SID and its identifier echo.

    The header is often printable (``0x62`` is ``b``, ``0x61`` is ``a``, a DID such
    as ``5A31`` is ``Z1``), and left in it would join the string that follows,
    giving a wrong offset, text and expression. An unknown service keeps every byte.
    """
    if not data:
        return 0
    layout = response_layout(data[0])
    return 0 if layout is None else min(layout.header_bytes, len(data))


def _data_runs(data: bytes, min_len: int = 1) -> list[tuple[int, str]]:
    """Printable runs of a payload's data bytes (never its header), as ``(start, text)``."""
    return folded_runs(ascii_fold(data, data_start(data)), min_len)


def _readings(runs: list[_Run], lo: int, hi: int) -> Counter[str]:
    """Weighted readings of ``[lo, hi)``; every run must cover that span."""
    texts: Counter[str] = Counter()
    for r in runs:
        texts[r.text[lo - r.start : hi - r.start]] += r.row.weight
    return texts


def _finalize(runs: list[_Run], lo: int, hi: int, *, kind: str, needle: bool) -> Finding:
    """One finding for the location ``[lo, hi)``, from every run that covers it.

    Runs come from distinct payload rows (a row's runs are disjoint, so at most one
    of them covers a span), which makes the summed weight a count of captures.
    """
    row = runs[0].row
    if kind == KIND_BYTES:
        # Every run in a byte-pattern group is the same matched bytes at the same
        # offset by construction (and its text is hex, not one character per byte).
        texts = Counter({row.data[lo:hi].hex().upper(): sum(r.row.weight for r in runs)})
    else:
        texts = _readings(runs, lo, hi)
    matches: Counter[str] = Counter()
    for r in runs:
        matches[r.match] += r.row.weight
    lens = {len(r.row.data) for r in runs}
    longest = max(lens)
    mixed = len({n <= _SINGLE_FRAME_MAX for n in lens}) > 1
    lossy = any(r.row.lossy for r in runs)
    expressible = kind == KIND_TEXT and not mixed and row.field == FIELD_PAYLOAD and not lossy
    return Finding(
        source="payload",
        field=row.field,
        ecu=row.ecu,
        pid=row.pid,
        text=texts.most_common(1)[0][0],
        hits=sum(r.row.weight for r in runs),
        first=min((r.row.first for r in runs if r.row.first), default=""),
        last=max((r.row.last for r in runs if r.row.last), default=""),
        match=matches.most_common(1)[0][0] if needle else None,
        offset=lo,
        length=hi - lo,
        payload_len=longest,
        variants=len(texts),
        expression=wican_expression(lo, hi - lo, longest) if expressible else None,
        lossy=lossy,
        mixed_frames=mixed,
    )


def _sorted(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (f.ecu, f.pid, f.field, f.offset or 0, f.text))


def find_in_payloads(
    rows: Iterable[PayloadRow],
    *,
    text_spans: TextSpans | None = None,
    byte_spans: ByteSpans | None = None,
    match_key: MatchKey | None = None,
) -> list[Finding]:
    """Findings for a needle: every location one of the matchers hits.

    ``text_spans`` runs over each printable run of the payload's data bytes on its
    own, so a match can never reach across a non-printable byte (a greedy regex would
    otherwise run into one and be discarded), and each hit is reported with its
    whole run, so a search for ``EHEL`` reports ``EHEL-MS2``. ``match_key`` decides
    which matched readings are the same hit (``str.lower`` for a case-insensitive
    search, so ``ehel`` and ``EHEL`` at one offset are one finding).

    ``byte_spans`` runs over the raw bytes and reports exactly the matched bytes,
    rendered as hex (which no ``ascii`` expression can read back, so it gets none).
    """
    groups: dict[tuple[str, str, str, str, int, str], list[_Run]] = defaultdict(list)
    for row in rows:
        where = (row.field, row.ecu, row.pid)
        if text_spans is not None:
            for start, text in _data_runs(row.data):
                for s, e in text_spans(text):
                    run = _Run(start, start + len(text), text, row, text[s:e])
                    key = match_key(run.match) if match_key else run.match
                    groups[(KIND_TEXT, *where, start + s, key)].append(run)
        if byte_spans is not None:
            for s, e in byte_spans(row.data):
                hexed = row.data[s:e].hex().upper()
                groups[(KIND_BYTES, *where, s, hexed)].append(_Run(s, e, hexed, row, hexed))
    findings = []
    for (kind, *_where), runs in groups.items():
        lo, hi = max(r.start for r in runs), min(r.end for r in runs)
        findings.append(_finalize(runs, lo, hi, kind=kind, needle=True))
    return _sorted(findings)


def inventory_payloads(
    rows: Iterable[PayloadRow], *, min_len: int, plausible_only: bool
) -> list[Finding]:
    """Every plausible string in the payloads, grouped into one row per location.

    Without the plausibility filter this is ``strings(1)``, which drowns in numeric
    telemetry that happens to be printable; the filter is what makes an inventory
    usable (see :func:`canlib.textscan.is_plausible_token`).
    """
    per_pid: dict[tuple[str, str, str], list[_Run]] = defaultdict(list)
    for row in rows:
        for start, text in _data_runs(row.data, min_len):
            if plausible_only and not is_plausible_token(text):
                continue
            per_pid[(row.field, row.ecu, row.pid)].append(_Run(start, start + len(text), text, row))
    findings: list[Finding] = []
    for runs in per_pid.values():
        for lo, hi in string_locations(runs):
            covering = [r for r in runs if r.start <= lo and r.end >= hi]
            findings.append(_finalize(covering, lo, hi, kind=KIND_TEXT, needle=False))
    return _sorted(findings)


def string_locations(runs: Iterable[_Run]) -> list[Span]:
    """The distinct string locations of one PID's runs, as each location's stable core.

    The leading and trailing bytes around a string often vary between captures while
    the middle is stable, so the run boundary moves (``' #EHEL-MS2'`` vs
    ``'EHEL-MS2'``); reporting the region every such run shares gives one row for one
    string instead of several near-duplicates.

    Runs join a location only while that shared region stays non-empty. Merging every
    *overlapping* run instead lets one capture's printable byte bridge two strings
    that are separate everywhere else; the merged region is then empty and one of the
    strings vanishes from the report. Sorted by start, a run that misses the current
    location's core starts at or after its end, so it cannot belong to any earlier
    location either.
    """
    locations: list[Span] = []
    for run in sorted(runs, key=lambda r: (r.start, r.end)):
        if locations:
            lo, hi = locations[-1]
            if run.start < hi and run.end > lo:
                locations[-1] = (max(lo, run.start), min(hi, run.end))
                continue
        locations.append((run.start, run.end))
    return locations


def snippet(text: str, span: Span) -> str:
    """``text`` trimmed to a window around ``span``, with ellipses where it was cut."""
    s, e = span
    lo = max(0, s - SNIPPET_CONTEXT)
    hi = min(len(text), e + SNIPPET_CONTEXT)
    flat = " ".join(text[lo:hi].split())
    return ("…" if lo > 0 else "") + flat + ("…" if hi < len(text) else "")


def find_in_text(rows: Iterable[TextRow], text_spans: TextSpans) -> list[Finding]:
    """Findings for a needle across free text (labels, notes, text responses)."""
    out: list[Finding] = []
    for row in rows:
        spans = text_spans(row.text)
        if not spans:
            continue
        s, e = spans[0]
        out.append(
            Finding(
                source="text",
                field=row.field,
                ecu=row.ecu,
                pid=row.pid,
                text=snippet(row.text, (s, e)),
                hits=row.weight,
                first=row.first,
                last=row.last,
                match=row.text[s:e],
            )
        )
    return sorted(out, key=lambda f: (f.field, f.ecu, f.pid, f.text))


def find_in_definitions(
    identities: Iterable[tuple[str, str, str]], text_spans: TextSpans
) -> list[Finding]:
    """Findings for a needle across ``ecus/`` identity fields: ``(ecu, field, value)``."""
    out: list[Finding] = []
    for ecu, field, value in identities:
        spans = text_spans(value)
        if not spans:
            continue
        s, e = spans[0]
        out.append(
            Finding(
                source="defs",
                field=f"identity.{field}",
                ecu=ecu,
                pid="",
                text=snippet(value, (s, e)),
                hits=1,
                first="",
                last="",
                match=value[s:e],
            )
        )
    return sorted(out, key=lambda f: (f.ecu, f.field))
