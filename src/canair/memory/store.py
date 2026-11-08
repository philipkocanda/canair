"""The dump store: one directory per dump, a ``dump.yaml`` manifest and the bytes read.

::

    <config dir>/memory/<profile>/<ECU>/<YYYYMMDD-HHMMSS>[-label]/
        dump.yaml     # what was asked, read, refused and skipped, and on what
        data.bin      # the bytes read, appended as each extent finishes

A dump is named by its id, ``<ECU>/<directory>``, relative to the profile's store.

A dump grows while it is read. :class:`DumpWriter` appends each finished extent to
``data.bin`` and then rewrites the manifest atomically, so a killed run loses at most the
extent in flight, and the manifest is the only truth: bytes in ``data.bin`` that no
manifest entry names are ignored. Each ``stretches:`` entry names its file and its offset
in it. A version-1 dump, one ``START-END.bin`` per stretch, still loads.

A dump made by ``canair memory image`` is a *job*: its manifest also holds the ``plan:``
it reads to (:mod:`canair.memory.job`), the ``rate:`` it had reached and its ``pauses:``.
A job that has not finished is resumable.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Final

import yaml

from canair.core import yaml_io
from canair.core.paths import config_dir
from canair.uds.memory import MemoryRequestError, parse_format

MANIFEST: Final = "dump.yaml"
DATA: Final = "data.bin"
MANIFEST_VERSION: Final = 2

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")
_Dumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


class DumpError(ValueError):
    """A dump that cannot be found, read or written."""


@dataclass(frozen=True)
class Stretch:
    """Contiguous bytes read from ``start``."""

    start: int
    data: bytes

    @property
    def end(self) -> int:
        return self.start + len(self.data) - 1


@dataclass(frozen=True)
class Gap:
    """Addresses not read: refused by the ECU (``result`` and ``nrc``), or skipped."""

    start: int
    end: int
    result: str
    nrc: int | None = None


@dataclass(frozen=True)
class PieceRef:
    """Where the bytes of ``start``-``end`` are stored: ``file`` from ``offset``."""

    start: int
    end: int
    file: str
    offset: int = 0


@dataclass(frozen=True)
class Extent:
    """One finished extent: the addresses it covers, the hash of what was read, how it went.

    ``verified`` is ``None`` until the extent is read a second time, then ``"same"``, or the
    2-of-3 verdict of :func:`canair.memory.verify.settle` when the reads disagreed.
    """

    start: int
    end: int
    sha256: str
    kind: str = "full"
    reads: int = 0
    events: tuple[str, ...] = ()
    verified: str | None = None


@dataclass
class Dump:
    """One dump, as written and read back."""

    ecu: str
    tx_id: int
    memory_format: str
    taken: str
    asked: list[tuple[int, int]]
    stretches: list[Stretch] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    software: str | None = None
    label: str | None = None
    profile: str | None = None
    variant: str | None = None
    revisions: dict[str, str] = field(default_factory=dict)
    canair: str | None = None
    transport: str | None = None
    complete: bool = True
    plan: dict[str, Any] | None = None
    rate: dict[str, Any] | None = None
    extents: list[Extent] = field(default_factory=list)
    pauses: list[dict[str, Any]] = field(default_factory=list)
    pieces: list[PieceRef] = field(default_factory=list)
    path: Path | None = None

    @property
    def id(self) -> str:
        return f"{self.ecu}/{self.path.name}" if self.path is not None else ""

    @property
    def bytes_read(self) -> int:
        return sum(len(s.data) for s in self.stretches)

    @property
    def resumable(self) -> bool:
        """A job (it has a plan) that has not finished."""
        return self.plan is not None and not self.complete

    def byte_map(self) -> dict[int, int]:
        """Every address read and its byte."""
        out: dict[int, int] = {}
        for s in self.stretches:
            for i, b in enumerate(s.data):
                out[s.start + i] = b
        return out

    def read_span(self, start: int, end: int) -> list[Stretch]:
        """The stretches read inside ``start``-``end``, cut to it."""
        out = []
        for s in self.stretches:
            lo, hi = max(s.start, start), min(s.end, end)
            if lo <= hi:
                out.append(Stretch(lo, s.data[lo - s.start : hi - s.start + 1]))
        return out


def store_root(profile_name: str) -> Path:
    """Where a profile's dumps live (user state, not a profile member)."""
    return config_dir() / "memory" / profile_name


def merge_stretches(pieces: list[Stretch]) -> list[Stretch]:
    """Join adjacent pieces (in address order) into contiguous stretches."""
    out: list[Stretch] = []
    for piece in sorted(pieces, key=lambda p: p.start):
        if out and out[-1].end + 1 == piece.start:
            out[-1] = Stretch(out[-1].start, out[-1].data + piece.data)
        else:
            out.append(piece)
    return out


def pieces_hash(pieces: list[Stretch]) -> str:
    """The SHA-256 of what was read: each stretch's address, length and bytes, in order.

    Adjacent pieces are joined first, so the hash depends on the bytes and where they are,
    not on how many reads brought them.
    """
    digest = hashlib.sha256()
    for p in merge_stretches([p for p in pieces if p.data]):
        digest.update(p.start.to_bytes(8, "big") + len(p.data).to_bytes(4, "big") + p.data)
    return digest.hexdigest()


def _hex(value: int, width: int) -> str:
    return f"0x{value:0{width}X}"


def _extent_doc(e: Extent, width: int) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "start": _hex(e.start, width),
        "end": _hex(e.end, width),
        "sha256": e.sha256,
        "kind": e.kind,
        "reads": e.reads,
    }
    if e.events:
        doc["events"] = list(e.events)
    if e.verified is not None:
        doc["verified"] = e.verified
    return doc


def _manifest(dump: Dump, width: int) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "manifest": MANIFEST_VERSION,
        "ecu": dump.ecu,
        "tx_id": _hex(dump.tx_id, 3),
        "memory_format": dump.memory_format,
        "software": dump.software,
        "taken": dump.taken,
        "label": dump.label,
        "profile": dump.profile,
        "variant": dump.variant,
        "revisions": dump.revisions or None,
        "canair": dump.canair,
        "transport": dump.transport,
        "complete": dump.complete,
        "asked": [[_hex(a, width), _hex(b, width)] for a, b in dump.asked],
        "plan": dump.plan,
        "rate": dump.rate,
        "stretches": [
            {"start": _hex(p.start, width), "end": _hex(p.end, width), "file": p.file}
            | ({"offset": p.offset} if p.offset else {})
            for p in dump.pieces
        ],
        "gaps": [
            {
                "start": _hex(g.start, width),
                "end": _hex(g.end, width),
                "result": g.result,
                **({"nrc": _hex(g.nrc, 2)} if g.nrc is not None else {}),
            }
            for g in dump.gaps
        ],
        "extents": [_extent_doc(e, width) for e in dump.extents] or None,
        "pauses": dump.pauses or None,
    }
    return {k: v for k, v in doc.items() if v is not None}


def _address_width(dump: Dump) -> int:
    try:
        return 2 * parse_format(dump.memory_format).address_bytes
    except MemoryRequestError:
        return 8


def _write_manifest(dump: Dump, path: Path, width: int) -> None:
    """Replace the manifest atomically: a crash leaves the old one or the new one."""
    text = yaml.dump(_manifest(dump, width), Dumper=_Dumper, sort_keys=False)
    tmp = path / f".{MANIFEST}.tmp"
    with tmp.open("w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path / MANIFEST)


def _cut(stretches: list[Stretch], lo: int, hi: int) -> list[Stretch]:
    """``stretches`` without the addresses ``lo``-``hi``."""
    out: list[Stretch] = []
    for s in stretches:
        if s.start < lo:
            out.append(Stretch(s.start, s.data[: min(len(s.data), lo - s.start)]))
        if s.end > hi:
            first = max(s.start, hi + 1)
            out.append(Stretch(first, s.data[first - s.start :]))
    return out


def _merge_gap(gaps: list[Gap], gap: Gap) -> None:
    last = gaps[-1] if gaps else None
    if last and (last.result, last.nrc) == (gap.result, gap.nrc) and last.end + 1 == gap.start:
        gaps[-1] = Gap(last.start, gap.end, gap.result, gap.nrc)
    else:
        gaps.append(gap)


class DumpWriter:
    """Grows one dump on disk: bytes appended to ``data.bin``, the manifest rewritten after.

    Every write is complete before the manifest names it, so the manifest on disk always
    describes bytes that are there.
    """

    def __init__(self, dump: Dump, width: int | None = None):
        if dump.path is None:
            raise DumpError("the dump has no directory yet")
        self.dump = dump
        self.width = width or _address_width(dump)
        self._data: BinaryIO = (dump.path / DATA).open("ab")

    @classmethod
    def create(cls, dump: Dump, root: Path, *, address_width: int = 8) -> DumpWriter:
        """Make a new directory for ``dump`` under ``root/<ECU>/`` and write its manifest."""
        stamp = datetime.fromisoformat(dump.taken).strftime("%Y%m%d-%H%M%S")
        slug = _SLUG.sub("-", dump.label or "").strip("-")[:40]
        base = root / dump.ecu / (f"{stamp}-{slug}" if slug else stamp)
        target, n = base, 1
        while target.exists():
            n += 1
            target = base.with_name(f"{base.name}-{n}")
        target.mkdir(parents=True)
        dump.path = target
        writer = cls(dump, address_width)
        writer.save()
        return writer

    def save(self) -> None:
        assert self.dump.path is not None
        _write_manifest(self.dump, self.dump.path, self.width)

    def _append(self, pieces: list[Stretch]) -> list[PieceRef]:
        refs: list[PieceRef] = []
        for p in merge_stretches([p for p in pieces if p.data]):
            offset = self._data.seek(0, os.SEEK_END)
            self._data.write(p.data)
            refs.append(PieceRef(p.start, p.end, DATA, offset))
        self._data.flush()
        os.fsync(self._data.fileno())
        return refs

    def _apply(self, refs: list[PieceRef], pieces: list[Stretch], gaps: list[Gap]) -> None:
        self.dump.pieces.extend(refs)
        self.dump.stretches = merge_stretches(
            [*self.dump.stretches, *(p for p in pieces if p.data)]
        )
        for gap in sorted(gaps, key=lambda g: g.start):
            _merge_gap(self.dump.gaps, gap)

    def add(self, pieces: list[Stretch], gaps: list[Gap], extent: Extent | None = None) -> None:
        """Store what one extent read (and its record), then rewrite the manifest."""
        self._apply(self._append(pieces), pieces, gaps)
        if extent is not None:
            self.dump.extents.append(extent)
        self.save()

    def replace(self, extent: Extent, pieces: list[Stretch]) -> None:
        """Store ``pieces`` in place of what was read in ``extent``'s span before."""
        lo, hi = extent.start, extent.end
        self.dump.pieces = [p for p in self.dump.pieces if not lo <= p.start <= hi]
        self.dump.stretches = _cut(self.dump.stretches, lo, hi)
        self._apply(self._append(pieces), pieces, [])
        self.update(extent)

    def update(self, extent: Extent) -> None:
        """Replace the record of the extent that starts where ``extent`` does."""
        self.dump.extents = [e for e in self.dump.extents if e.start != extent.start] + [extent]
        self.dump.extents.sort(key=lambda e: e.start)
        self.save()

    def finish(self, complete: bool, rest: Iterable[Gap] = ()) -> None:
        """Record what was not read as ``rest``, mark the dump, write it and close."""
        for gap in rest:
            _merge_gap(self.dump.gaps, gap)
        self.dump.gaps.sort(key=lambda g: g.start)
        self.dump.complete = complete
        self.save()
        self.close()

    def close(self) -> None:
        if not self._data.closed:
            self._data.close()


def save_dump(dump: Dump, root: Path, *, address_width: int = 8) -> Path:
    """Write ``dump`` (already read whole) as a new directory under ``root/<ECU>/``."""
    stretches, gaps, complete = list(dump.stretches), list(dump.gaps), dump.complete
    dump.stretches, dump.gaps, dump.pieces = [], [], []
    writer = DumpWriter.create(dump, root, address_width=address_width)
    writer.add(stretches, gaps)
    writer.finish(complete)
    assert dump.path is not None
    return dump.path


def _int(value: Any) -> int:
    return value if isinstance(value, int) else int(str(value), 0)


def _pieces(path: Path, entries: list[Mapping[str, Any]]) -> tuple[list[PieceRef], list[Stretch]]:
    refs: list[PieceRef] = []
    data: list[Stretch] = []
    files: dict[str, bytes] = {}
    for entry in entries:
        start, name = _int(entry["start"]), str(entry["file"])
        if name not in files:
            files[name] = (path / name).read_bytes()
        blob = files[name]
        offset = int(entry.get("offset") or 0)
        end = _int(entry["end"]) if entry.get("end") is not None else start + len(blob) - 1
        chunk = blob[offset : offset + end - start + 1]
        if len(chunk) != end - start + 1:
            raise DumpError(f"{name} is shorter than the manifest says (0x{start:X}-0x{end:X})")
        refs.append(PieceRef(start, end, name, offset))
        data.append(Stretch(start, chunk))
    return refs, merge_stretches(data)


def _extent(entry: Mapping[str, Any]) -> Extent:
    return Extent(
        _int(entry["start"]),
        _int(entry["end"]),
        str(entry.get("sha256", "")),
        str(entry.get("kind", "full")),
        int(entry.get("reads") or 0),
        tuple(str(e) for e in entry.get("events") or ()),
        None if entry.get("verified") is None else str(entry["verified"]),
    )


def load_dump(path: Path) -> Dump:
    """Read the dump in directory ``path``; raises :class:`DumpError` if it is not one."""
    manifest = path / MANIFEST
    if not manifest.is_file():
        raise DumpError(f"{path} holds no {MANIFEST}")
    try:
        doc = yaml_io.safe_load(manifest.read_text()) or {}
        refs, stretches = _pieces(path, doc.get("stretches") or [])
        gaps = [
            Gap(
                _int(g["start"]),
                _int(g["end"]),
                str(g.get("result", "")),
                _int(g["nrc"]) if g.get("nrc") is not None else None,
            )
            for g in doc.get("gaps") or []
        ]
        return Dump(
            ecu=str(doc["ecu"]),
            tx_id=_int(doc["tx_id"]),
            memory_format=str(doc.get("memory_format", "")),
            taken=str(doc.get("taken", "")),
            asked=[(_int(a), _int(b)) for a, b in doc.get("asked") or []],
            stretches=stretches,
            gaps=gaps,
            software=doc.get("software"),
            label=doc.get("label"),
            profile=doc.get("profile"),
            variant=doc.get("variant"),
            revisions=dict(doc.get("revisions") or {}),
            canair=doc.get("canair"),
            transport=doc.get("transport"),
            complete=bool(doc.get("complete", True)),
            plan=doc.get("plan"),
            rate=doc.get("rate"),
            extents=[_extent(e) for e in doc.get("extents") or []],
            pauses=list(doc.get("pauses") or []),
            pieces=refs,
            path=path,
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as e:
        raise DumpError(f"{path}: not a readable dump ({e})") from e


def iter_dumps(root: Path, ecu: str | None = None) -> Iterator[Dump]:
    """Every readable dump under ``root``, oldest first (optionally one ECU's)."""
    if not root.is_dir():
        return
    for ecu_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if ecu is not None and ecu_dir.name.upper() != ecu.upper():
            continue
        for path in sorted(p for p in ecu_dir.iterdir() if (p / MANIFEST).is_file()):
            try:
                yield load_dump(path)
            except DumpError:
                continue


def resolve_dump(root: Path, ref: str) -> Dump:
    """The dump ``ref`` names: a directory path, an id (``BCM/2026…``), or a unique prefix."""
    as_path = Path(ref).expanduser()
    if (as_path / MANIFEST).is_file():
        return load_dump(as_path)
    if (root / ref / MANIFEST).is_file():
        return load_dump(root / ref)
    matches = [
        d for d in iter_dumps(root) if d.id.startswith(ref) or d.id.split("/")[1].startswith(ref)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise DumpError(f"no dump {ref!r} (list them with `canair memory ls`)")
    raise DumpError(f"{ref!r} names {len(matches)} dumps: {', '.join(d.id for d in matches)}")
