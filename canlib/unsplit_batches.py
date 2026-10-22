"""Spot multi-DID replies stored whole under their first DID.

A batched ``22 D1 D2 …`` reply starts ``62 D1``, exactly like a single ``22 D1`` reply, so a
batch reply that leaked into the next single read's slot passed the echo check and was saved
as ``D1``'s payload, with the other DIDs' data appended. Every analysis then reads ``D1`` past
its real end. Older ``wican-ws`` monitor sessions did this when a batch timed out and the late
reply was taken as the answer to the per-DID fallback read.

Nothing in the stored row says how it was requested, so this works across the corpus, like
:mod:`canlib.misfiled`: a ``22`` capture is flagged when it is longer than its DID's
established length and the remainder walks, DID by DID, through other DIDs of the same ECU at
their own established lengths to the end of the payload (:func:`canlib.multi_did.split_chain`).

The repair, :func:`split_unsplit_batches`, replaces each such row with one row per DID at the
same instant, so the readings it carried are kept rather than deleted. Detection is pure; only
the repair touches disk.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import capture_io
from .capture_types import CaptureRecord
from .misfiled import PayloadSample
from .multi_did import is_fill, split_chain
from .payload_corpus import Locator, load_payload_corpus

# A DID's data length is "established" only once it is seen often and almost exclusively, so
# a thinly sampled or variable-length DID never anchors a finding.
MIN_LENGTH_SAMPLES = 5
MIN_LENGTH_SHARE = 0.9

READ_DID_PREFIX = "22"


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


def _data_lengths(samples: list[PayloadSample]) -> dict[tuple[int, str], int]:
    """``(rx, DID4) -> data length`` for every DID whose positive replies agree on one.

    A padded legacy reply (the length plus a run of fill) agrees with that length, so the
    padded and unpadded eras of one DID do not split its vote.
    """
    payloads: dict[tuple[int, str], list[bytes]] = defaultdict(list)
    for s in samples:
        did = _did(s.pid)
        if did and len(s.payload) >= 3 and s.payload[0] == 0x62:
            payloads[(s.rx, did)].append(s.payload)
    out: dict[tuple[int, str], int] = {}
    for key, group in payloads.items():
        if len(group) < MIN_LENGTH_SAMPLES:
            continue
        length = Counter(len(p) for p in group).most_common(1)[0][0]
        agree = sum(1 for p in group if len(p) >= length and is_fill(p[length:]))
        if agree / len(group) >= MIN_LENGTH_SHARE:
            out[key] = length - 3
    return out


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


@dataclass
class SplitReport:
    """What :func:`split_unsplit_batches` did, file by file."""

    split: list[Split] = field(default_factory=list)
    # Found in a layer writes may not touch (a layered profile's read-only base).
    read_only: list[Split] = field(default_factory=list)


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


def _under(path: Path, directory: Path) -> bool:
    return path.resolve().is_relative_to(directory.resolve())


def split_unsplit_batches(
    read_dirs: list[Path], write_dir: Path, *, dry_run: bool = False
) -> SplitReport:
    """Replace every unsplit multi-DID row under ``write_dir`` with its per-DID rows.

    Lengths are established over every file in ``read_dirs`` (the whole layered history), but
    only files under ``write_dir`` are rewritten; a finding elsewhere is reported in
    ``read_only``. Idempotent: a split row is an ordinary single-DID reply and is not found
    again.
    """
    files = [p for d in read_dirs if d.is_dir() for p in capture_io.iter_capture_files(d)]
    corpus = load_payload_corpus(files)
    by_path: dict[Path, list[tuple[Split, UnsplitBatch]]] = defaultdict(list)
    report = SplitReport()
    for u in find_unsplit_batches(corpus.samples):
        at = corpus.where[u.sample.handle]
        split = Split(at, u.sample.rx, u.sample.pid.upper(), tuple(u.pids))
        if _under(at.path, write_dir):
            by_path[at.path].append((split, u))
        else:
            report.read_only.append(split)
    for path, found in by_path.items():
        data = capture_io.load_capture_file(path)
        # Last row first, so replacing one row never shifts the index of another still to do.
        for split, u in sorted(found, key=lambda f: (f[0].where.session, f[0].where.capture))[::-1]:
            caps = data["sessions"][split.where.session]["captures"]
            caps[split.where.capture : split.where.capture + 1] = _per_did_rows(
                caps[split.where.capture], u.parts
            )
            report.split.append(split)
        if not dry_run:
            capture_io.dump_capture_file(path, data)
    report.split.sort(key=lambda s: (str(s.where.path), s.where.session, s.where.capture))
    return report
