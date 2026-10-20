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
from canlib.textscan import (
    PLACEHOLDER,
    Span,
    ascii_fold,
    enclosing_run,
    is_plausible_token,
    printable_runs,
)

# A single-frame response (<= 7 bytes) carries one PCI byte, a multi-frame one two.
# Both the WiCAN expression and the run's byte labels depend on which, so a group
# that mixes the two layouts has no one correct expression.
_SINGLE_FRAME_MAX = 7

SNIPPET_CONTEXT = 24

KIND_TEXT = "text"
KIND_BYTES = "bytes"


@dataclass(frozen=True)
class PayloadRow:
    """One distinct ``(ecu, pid, payload)`` and how often / when it was captured."""

    ecu: str
    pid: str
    data: bytes
    weight: int
    first: str
    last: str


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
    lossy: bool = False


@dataclass(frozen=True)
class Finding:
    """One distinct location a string was found.

    ``offset``/``length`` are an ISO-TP payload index and byte count (payload hits
    only); ``expression`` is the paste-ready WiCAN ``[Bn:Bm]`` range for that run, or
    ``None`` when no single expression is correct for every capture in the group.
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


@dataclass
class _Run:
    """A printable (or matched) byte run found in one distinct payload."""

    start: int
    end: int
    text: str
    weight: int
    first: str
    last: str
    payload_len: int


TextSpans = Callable[[str], list[Span]]
ByteSpans = Callable[[bytes], list[Span]]


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


def _core(runs: list[_Run]) -> tuple[int, int, str, int]:
    """The stable core of a cluster of overlapping runs: ``(start, end, text, variants)``.

    The leading and trailing bytes around a string often vary between captures while
    the middle is stable, so the run boundary moves (``' #EHEL-MS2'`` vs ``'EHEL-MS2'``).
    Reporting the intersection gives one row for one string instead of several
    near-duplicates. The text is the most common reading of that region and
    ``variants`` counts how many distinct readings there were.
    """
    lo = max(r.start for r in runs)
    hi = min(r.end for r in runs)
    if hi <= lo:  # disjoint chain: no common region, fall back to the commonest run
        best = max(runs, key=lambda r: r.weight)
        lo, hi = best.start, best.end
    texts: Counter[str] = Counter()
    for r in runs:
        texts[r.text[lo - r.start : hi - r.start]] += r.weight
    return lo, hi, texts.most_common(1)[0][0], len(texts)


def _finalize(
    ecu: str, pid: str, runs: list[_Run], *, match: str | None, kind: str = KIND_TEXT
) -> Finding:
    if kind == KIND_BYTES:
        # Every run in a byte-pattern group is the same matched bytes at the same
        # offset by construction, so there is no boundary to reconcile.
        first = runs[0]
        lo, hi, text, variants = first.start, first.end, first.text, 1
    else:
        lo, hi, text, variants = _core(runs)
    lens = {r.payload_len for r in runs}
    longest = max(lens)
    uniform = len({n <= _SINGLE_FRAME_MAX for n in lens}) == 1
    expressible = kind == KIND_TEXT and uniform
    expression = wican_expression(lo, hi - lo, longest) if expressible else None
    return Finding(
        source="payload",
        field="payload",
        ecu=ecu,
        pid=pid,
        text=text,
        hits=sum(r.weight for r in runs),
        first=min((r.first for r in runs if r.first), default=""),
        last=max((r.last for r in runs if r.last), default=""),
        match=match,
        offset=lo,
        length=hi - lo,
        payload_len=longest,
        variants=variants,
        expression=expression,
    )


def _sorted(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (f.ecu, f.pid, f.offset or 0, f.text))


def find_in_payloads(
    rows: Iterable[PayloadRow],
    *,
    text_spans: TextSpans | None = None,
    byte_spans: ByteSpans | None = None,
) -> list[Finding]:
    """Findings for a needle: every location one of the matchers hits.

    ``text_spans`` runs over the payload's printable view and each hit is widened to
    its enclosing printable run, so a search for ``EHEL`` reports ``EHEL-MS2``.
    ``byte_spans`` runs over the raw bytes and reports exactly the matched bytes,
    rendered as hex (which no ``ascii`` expression can read back, so it gets none).
    """
    groups: dict[tuple[str, str, str, int, str], list[_Run]] = defaultdict(list)
    for row in rows:
        n = len(row.data)
        if text_spans is not None:
            folded = ascii_fold(row.data)
            for s, e in text_spans(folded):
                if PLACEHOLDER in folded[s:e]:
                    continue  # a text match is printable by definition
                rs, re_ = enclosing_run(folded, s, e)
                run = _Run(rs, re_, folded[rs:re_], row.weight, row.first, row.last, n)
                groups[(KIND_TEXT, row.ecu, row.pid, s, folded[s:e])].append(run)
        if byte_spans is not None:
            for s, e in byte_spans(row.data):
                hexed = row.data[s:e].hex().upper()
                run = _Run(s, e, hexed, row.weight, row.first, row.last, n)
                groups[(KIND_BYTES, row.ecu, row.pid, s, hexed)].append(run)
    return _sorted(
        _finalize(ecu, pid, runs, match=match, kind=kind)
        for (kind, ecu, pid, _start, match), runs in groups.items()
    )


def inventory_payloads(
    rows: Iterable[PayloadRow], *, min_len: int, plausible_only: bool
) -> list[Finding]:
    """Every plausible string in the payloads, grouped into one row per location.

    Without the plausibility filter this is ``strings(1)``, which drowns in numeric
    telemetry that happens to be printable; the filter is what makes an inventory
    usable (see :func:`canlib.textscan.is_plausible_token`).
    """
    per_pid: dict[tuple[str, str], list[_Run]] = defaultdict(list)
    for row in rows:
        n = len(row.data)
        for start, text in printable_runs(row.data, min_len):
            if plausible_only and not is_plausible_token(text):
                continue
            per_pid[(row.ecu, row.pid)].append(
                _Run(start, start + len(text), text, row.weight, row.first, row.last, n)
            )
    findings: list[Finding] = []
    for (ecu, pid), runs in per_pid.items():
        for cluster in _overlap_clusters(runs):
            findings.append(_finalize(ecu, pid, cluster, match=None))
    return _sorted(findings)


def _overlap_clusters(runs: list[_Run]) -> list[list[_Run]]:
    """Group runs whose byte ranges overlap — one cluster per string location."""
    clusters: list[list[_Run]] = []
    reach = -1
    for run in sorted(runs, key=lambda r: (r.start, r.end)):
        if clusters and run.start < reach:
            clusters[-1].append(run)
            reach = max(reach, run.end)
        else:
            clusters.append([run])
            reach = run.end
    return clusters


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
                lossy=row.lossy,
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
