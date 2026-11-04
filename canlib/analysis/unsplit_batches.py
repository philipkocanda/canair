"""Spot multi-DID replies stored whole under their first DID.

A batched ``22 D1 D2 …`` reply starts ``62 D1``, exactly like a single ``22 D1`` reply, so a
batch reply that leaked into the next single read's slot passed the echo check and was saved
as ``D1``'s payload, with the other DIDs' data appended. Every analysis then reads ``D1`` past
its real end. Older ``wican-ws`` monitor sessions did this when a batch timed out and the late
reply was taken as the answer to the per-DID fallback read.

Nothing in the stored row says how it was requested, so this works across the corpus, like
:mod:`canlib.analysis.misfiled`: a ``22`` capture is flagged when it is longer than its DID's
established length and the remainder walks, DID by DID, through other DIDs of the same ECU at
their own established lengths to the end of the payload (:func:`canlib.uds.multi_did.split_chain`).

The repair, :func:`split_unsplit_batches`, replaces each such row with one row per DID at the
same instant, so the readings it carried are kept rather than deleted. Detection is pure; only
the repair touches disk.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from canlib.analysis.misfiled import PayloadSample
from canlib.captures import io as capture_io
from canlib.captures import merge as captures_merge
from canlib.captures.payload_corpus import Locator, load_payload_corpus
from canlib.captures.types import CaptureRecord, CaptureSession
from canlib.keepmode import KEEP_CHANGES, KEEP_UNIQUE
from canlib.uds.multi_did import POSITIVE_READ_SID, is_fill, is_padding, split_chain

# A DID's data length is "established" only once it is seen often and almost exclusively, so
# a thinly sampled or variable-length DID never anchors a finding.
MIN_LENGTH_SAMPLES = 5
MIN_LENGTH_SHARE = 0.9

# Lengths explain batch rows, which can establish further lengths; a corpus settles in two or
# three rounds, and the bound only keeps a pathological one from looping.
MAX_LENGTH_ROUNDS = 8

READ_DID_PREFIX = "22"

_Key = tuple[int, str]  # (rx, DID4)


@dataclass(frozen=True)
class UnsplitBatch:
    """A stored ``pid`` payload that is really a reply to several DIDs."""

    sample: PayloadSample
    # The single-DID replies it splits into, first DID first.
    parts: tuple[tuple[str, bytes], ...]

    @property
    def pids(self) -> list[str]:
        return [READ_DID_PREFIX + did for did, _ in self.parts]


def _did(pid: str) -> str | None:
    pid = pid.upper()
    if len(pid) == 6 and pid.startswith(READ_DID_PREFIX):
        return pid[2:]
    return None


def _did_replies(samples: list[PayloadSample]) -> dict[_Key, list[bytes]]:
    """Every positive reply that echoes the DID it is stored under, per ``(rx, DID4)``."""
    out: dict[_Key, list[bytes]] = defaultdict(list)
    for s in samples:
        did = _did(s.pid)
        if (
            did
            and len(s.payload) >= 3
            and s.payload[0] == POSITIVE_READ_SID
            and s.payload[1:3].hex().upper() == did
        ):
            out[(s.rx, did)].append(s.payload)
    return out


def _estimate(group: list[bytes], chains: Callable[[bytes, int], bool]) -> int | None:
    """The data length ``group`` establishes for its DID, or ``None``.

    The shortest observed length wins once at least :data:`MIN_LENGTH_SHARE` of the replies
    are explained by it: a reply of exactly that length, or that length stored with its ISO-TP
    padding (:func:`~canlib.uds.multi_did.is_padding`: fill out to the end of the final frame,
    never further), or one that ``chains(reply, data_len)`` shows to be a whole batch.
    Padded replies count towards their length, so a padded majority does not outvote the
    unpadded replies it agrees with; and batch rows count as explained, not against, so the
    leaked replies this module looks for cannot themselves stop their DID's length from being
    established. :data:`MIN_LENGTH_SAMPLES` replies must match it directly, so batches alone
    never establish a length. Padding is held to its frame because data often ends in ``00``:
    a looser rule would let one truncated reply make every full one look padded.
    """
    for total in sorted({len(p) for p in group}):
        if total <= 3:
            continue  # a DID's data record is never empty
        direct = sum(1 for p in group if is_padding(p, total))
        if direct < MIN_LENGTH_SAMPLES:
            continue
        batches = sum(
            1 for p in group if len(p) > total and not is_padding(p, total) and chains(p, total - 3)
        )
        if direct + batches >= MIN_LENGTH_SHARE * len(group):
            return total - 3
    return None


def _data_lengths(samples: list[PayloadSample]) -> dict[_Key, int]:
    """``(rx, DID4) -> data length`` for every DID whose positive replies establish one.

    Settled to a fixed point: the first round sees no batches (nothing chains without other
    DIDs' lengths), and each later one lets the lengths established so far explain the batch
    rows of DIDs that did not yet reach :data:`MIN_LENGTH_SHARE`.
    """
    groups = _did_replies(samples)
    lengths: dict[_Key, int] = {}
    for _ in range(MAX_LENGTH_ROUNDS):
        settled: dict[_Key, int] = {}
        for key, group in groups.items():
            dlen = _estimate(group, _chains_through(lengths, key))
            if dlen is not None:
                settled[key] = dlen
        if settled == lengths:
            break
        lengths = settled
    return lengths


def _chains_through(lengths: dict[_Key, int], key: _Key) -> Callable[[bytes, int], bool]:
    """Does a reply, read with ``key``'s DID at a candidate length, walk as a whole batch?"""
    rx, did = key

    def chains(payload: bytes, dlen: int) -> bool:
        return (
            split_chain(payload, lambda d: dlen if d == did else lengths.get((rx, d))) is not None
        )

    return chains


def find_unsplit_batches(samples: Iterable[PayloadSample]) -> list[UnsplitBatch]:
    """Return every sample that is a multi-DID reply stored under one DID, in input order."""
    pool = list(samples)
    lengths = _data_lengths(pool)
    findings: list[UnsplitBatch] = []
    for s in pool:
        did = _did(s.pid)
        own = lengths.get((s.rx, did)) if did else None
        if own is None or len(s.payload) <= own + 3 or s.payload[1:3].hex().upper() != did:
            continue
        if is_fill(s.payload[own + 3 :]):
            continue  # a padded reply, not a batch
        parts = split_chain(s.payload, lambda d, rx=s.rx: lengths.get((rx, d)))
        if parts is not None:
            findings.append(UnsplitBatch(s, tuple(parts)))
    return findings


@dataclass(frozen=True)
class Split:
    """One stored row that is (or, on a dry run, would be) replaced by per-DID rows."""

    where: Locator
    rx: int
    pid: str
    pids: tuple[str, ...]
    # Of ``pids``, those not written because a dedup-recorded session (``keep_mode``) already
    # holds the same value for them: the session stores a value only when it changes.
    unchanged: tuple[str, ...] = ()


@dataclass
class SplitReport:
    """What :func:`split_unsplit_batches` did, file by file."""

    split: list[Split] = field(default_factory=list)
    # Found in a layer writes may not touch (a layered profile's read-only base).
    read_only: list[Split] = field(default_factory=list)
    # Found in a writable session that also exists, identically, in a read-only layer. Readers
    # keep such a session once; splitting only the writable copy would make the two differ, so
    # every reading in it would be counted twice and the read-only copy would still be flagged.
    shadowed: list[Split] = field(default_factory=list)


def _per_did_rows(cap: CaptureRecord, parts: tuple[tuple[str, bytes], ...]) -> list[CaptureRecord]:
    """``cap`` once per DID, in its field order, each with that DID's ``pid`` and payload.

    ``elapsed_ms`` is dropped: one round trip answered every DID, so it times none of them.
    """
    rows: list[CaptureRecord] = []
    for did, reply in parts:
        row = cap.copy()
        row.pop("elapsed_ms", None)
        row["pid"] = READ_DID_PREFIX + did
        row["payload"] = reply.hex().upper()
        rows.append(row)
    return rows


def _dedup_key(cap: CaptureRecord) -> tuple[int, str, str] | None:
    """``(rx, PID, payload)`` as the recording dedup compares rows, or ``None`` for no payload."""
    from canlib.ecus import parse_ecu_ref

    rx = parse_ecu_ref(capture_io.capture_rx(cap) or None)
    pid, payload = cap.get("pid"), cap.get("payload")
    if rx is None or not pid or not payload:
        return None
    return rx, str(pid).upper(), str(payload).upper()


def _rebuild(
    session: CaptureSession, found: dict[int, UnsplitBatch]
) -> tuple[list[CaptureRecord], dict[int, tuple[str, ...]]]:
    """``session``'s captures with each found row split, and the parts each split left out.

    A session recorded with a dedup ``keep_mode`` stores a row only when its value changes
    (``changes``) or is new (``unique``), per ECU and PID. A split part that the session would
    not have stored is left out, so the split keeps that promise; any other session keeps every
    part. Original rows are never dropped.
    """
    keep = session.get("keep_mode")
    last: dict[tuple[int, str], str] = {}
    seen: set[tuple[int, str, str]] = set()
    out: list[CaptureRecord] = []
    unchanged: dict[int, tuple[str, ...]] = {}
    for ci, cap in enumerate(session["captures"]):
        u = found.get(ci)
        left_out: list[str] = []
        for row in [cap] if u is None else _per_did_rows(cap, u.parts):
            key = _dedup_key(row)
            if key is not None:
                rx, pid, payload = key
                redundant = (keep == KEEP_CHANGES and last.get((rx, pid)) == payload) or (
                    keep == KEEP_UNIQUE and key in seen
                )
                if u is not None and redundant:
                    left_out.append(pid)
                    continue
                last[(rx, pid)] = payload
                seen.add(key)
            out.append(row)
        if left_out:
            unchanged[ci] = tuple(left_out)
    return out, unchanged


def _under(path: Path, directory: Path) -> bool:
    return path.resolve().is_relative_to(directory.resolve())


def _session_keys(paths: Iterable[Path]) -> set[str]:
    keys: set[str] = set()
    for path in paths:
        try:
            data = capture_io.load_capture_file(path)
        except (OSError, ValueError):
            continue
        for session in data.get("sessions", []) if isinstance(data, dict) else []:
            keys.add(captures_merge.session_key(session))
    return keys


def split_unsplit_batches(
    read_dirs: list[Path], write_dir: Path, *, dry_run: bool = False
) -> SplitReport:
    """Replace every unsplit multi-DID row under ``write_dir`` with its per-DID rows.

    Lengths are established over every file in ``read_dirs`` (the whole layered history), but
    only files under ``write_dir`` are rewritten; a finding elsewhere is reported in
    ``read_only``, and one in a session a read-only layer holds identically in ``shadowed``.
    Idempotent: a split row is an ordinary single-DID reply and is not found again.
    """
    files = [p for d in read_dirs if d.is_dir() for p in capture_io.iter_capture_files(d)]
    corpus = load_payload_corpus(files)
    by_path: dict[Path, dict[int, dict[int, UnsplitBatch]]] = defaultdict(lambda: defaultdict(dict))
    report = SplitReport()

    def _split(u: UnsplitBatch, unchanged: tuple[str, ...] = ()) -> Split:
        at = corpus.where[u.sample.handle]
        return Split(at, u.sample.rx, u.sample.pid.upper(), tuple(u.pids), unchanged)

    for u in find_unsplit_batches(corpus.samples):
        at = corpus.where[u.sample.handle]
        if _under(at.path, write_dir):
            by_path[at.path][at.session][at.capture] = u
        else:
            report.read_only.append(_split(u))

    base_keys: set[str] | None = None
    for path, sessions in by_path.items():
        data = capture_io.load_capture_file(path)
        changed = False
        for si, found in sessions.items():
            session = data["sessions"][si]
            if base_keys is None:
                base_keys = _session_keys(p for p in files if not _under(p, write_dir))
            if captures_merge.session_key(session) in base_keys:
                report.shadowed.extend(_split(u) for _, u in sorted(found.items()))
                continue
            rows, unchanged = _rebuild(session, found)
            session["captures"] = rows
            changed = True
            report.split.extend(_split(u, unchanged.get(ci, ())) for ci, u in sorted(found.items()))
        if changed and not dry_run:
            capture_io.dump_capture_file(path, data)
    for found_list in (report.split, report.shadowed):
        found_list.sort(key=lambda s: (str(s.where.path), s.where.session, s.where.capture))
    return report
