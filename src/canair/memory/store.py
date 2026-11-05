"""The dump store: one directory per dump, a ``dump.yaml`` manifest and ``.bin`` stretches.

::

    <config dir>/memory/<profile>/<ECU>/<YYYYMMDD-HHMMSS>[-label]/
        dump.yaml                 # what was asked, read, refused and skipped, and on what
        FEDE0000-FEDE7FFF.bin     # one file per contiguous stretch that was read

A dump is named by its id, ``<ECU>/<directory>``, relative to the profile's store.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import yaml

from canair.core import yaml_io
from canair.core.paths import config_dir

MANIFEST: Final = "dump.yaml"
MANIFEST_VERSION: Final = 1

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


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
    path: Path | None = None

    @property
    def id(self) -> str:
        return f"{self.ecu}/{self.path.name}" if self.path is not None else ""

    @property
    def bytes_read(self) -> int:
        return sum(len(s.data) for s in self.stretches)

    def byte_map(self) -> dict[int, int]:
        """Every address read and its byte."""
        out: dict[int, int] = {}
        for s in self.stretches:
            for i, b in enumerate(s.data):
                out[s.start + i] = b
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


def _hex(value: int, width: int) -> str:
    return f"0x{value:0{width}X}"


def _manifest(dump: Dump, width: int, files: list[str]) -> dict[str, Any]:
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
        "stretches": [
            {"start": _hex(s.start, width), "end": _hex(s.end, width), "file": f}
            for s, f in zip(dump.stretches, files, strict=True)
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
    }
    return {k: v for k, v in doc.items() if v is not None}


def save_dump(dump: Dump, root: Path, *, address_width: int = 8) -> Path:
    """Write ``dump`` as a new directory under ``root/<ECU>/``; returns the directory."""
    stamp = datetime.fromisoformat(dump.taken).strftime("%Y%m%d-%H%M%S")
    slug = _SLUG.sub("-", dump.label or "").strip("-")[:40]
    base = root / dump.ecu / (f"{stamp}-{slug}" if slug else stamp)
    target, n = base, 1
    while target.exists():
        n += 1
        target = base.with_name(f"{base.name}-{n}")
    target.mkdir(parents=True)
    files = []
    for s in dump.stretches:
        name = f"{s.start:0{address_width}X}-{s.end:0{address_width}X}.bin"
        (target / name).write_bytes(s.data)
        files.append(name)
    text = yaml.safe_dump(_manifest(dump, address_width, files), sort_keys=False)
    (target / MANIFEST).write_text(text)
    dump.path = target
    return target


def _int(value: Any) -> int:
    return value if isinstance(value, int) else int(str(value), 0)


def load_dump(path: Path) -> Dump:
    """Read the dump in directory ``path``; raises :class:`DumpError` if it is not one."""
    manifest = path / MANIFEST
    if not manifest.is_file():
        raise DumpError(f"{path} holds no {MANIFEST}")
    try:
        doc = yaml_io.safe_load(manifest.read_text()) or {}
        stretches = [
            Stretch(_int(s["start"]), (path / s["file"]).read_bytes())
            for s in doc.get("stretches") or []
        ]
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
