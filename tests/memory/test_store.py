"""Tests for canair.memory — the dump store, diff and export (synthetic dumps only)."""

from __future__ import annotations

import pytest

from canair.memory.diff import diff_dumps
from canair.memory.export import to_bin, to_ihex, to_srec
from canair.memory.store import (
    DATA,
    MANIFEST,
    Dump,
    DumpError,
    DumpWriter,
    Extent,
    Gap,
    Stretch,
    iter_dumps,
    load_dump,
    merge_stretches,
    pieces_hash,
    resolve_dump,
    save_dump,
)


def _dump(stretches, *, software="F195:1", gaps=(), label="t", taken="2026-11-05T22:00:00"):
    return Dump(
        ecu="BCM",
        tx_id=0x7A0,
        memory_format="24",
        taken=taken,
        asked=[(0x1000, 0x10FF)],
        stretches=list(stretches),
        gaps=list(gaps),
        software=software,
        label=label,
    )


class TestStore:
    def test_round_trip(self, tmp_path):
        dump = _dump(
            [Stretch(0x1000, b"\x01\x02"), Stretch(0x1080, b"\x03")],
            gaps=[Gap(0x1002, 0x107F, "out_of_range", 0x31)],
        )
        path = save_dump(dump, tmp_path)
        assert path.parent.name == "BCM" and (path / DATA).read_bytes() == b"\x01\x02\x03"
        back = load_dump(path)
        assert back.stretches == dump.stretches and back.gaps == dump.gaps
        assert back.software == "F195:1" and back.id == f"BCM/{path.name}"

    def test_two_dumps_in_one_second_do_not_collide(self, tmp_path):
        a = save_dump(_dump([Stretch(0, b"a")]), tmp_path)
        b = save_dump(_dump([Stretch(0, b"b")]), tmp_path)
        assert a != b and len(list(iter_dumps(tmp_path))) == 2

    def test_resolve(self, tmp_path):
        path = save_dump(_dump([Stretch(0, b"a")], label="lights on"), tmp_path)
        assert resolve_dump(tmp_path, f"BCM/{path.name}").path == path
        assert resolve_dump(tmp_path, str(path)).path == path
        assert resolve_dump(tmp_path, "BCM/2026").path == path
        with pytest.raises(DumpError, match="no dump"):
            resolve_dump(tmp_path, "VCU/x")

    def test_merge_stretches(self):
        merged = merge_stretches([Stretch(2, b"c"), Stretch(0, b"ab"), Stretch(5, b"x")])
        assert merged == [Stretch(0, b"abc"), Stretch(5, b"x")]


class TestIncrementalWrites:
    def _writer(self, tmp_path):
        dump = _dump([])
        dump.complete = False
        return DumpWriter.create(dump, tmp_path, address_width=8)

    def test_each_extent_is_on_disk_once_it_is_added(self, tmp_path):
        writer = self._writer(tmp_path)
        writer.add([Stretch(0x1000, b"ab")], [], Extent(0x1000, 0x1001, "h1"))
        assert writer.dump.path is not None
        back = load_dump(writer.dump.path)
        assert back.stretches == [Stretch(0x1000, b"ab")] and not back.complete
        assert [e.start for e in back.extents] == [0x1000]
        writer.add([Stretch(0x1002, b"cd")], [Gap(0x1004, 0x1007, "out_of_range", 0x31)])
        writer.finish(True)
        back = load_dump(writer.dump.path)
        assert back.stretches == [Stretch(0x1000, b"abcd")] and back.complete
        assert back.gaps == [Gap(0x1004, 0x1007, "out_of_range", 0x31)]

    def test_bytes_no_manifest_names_are_ignored(self, tmp_path):
        """A crash after the append and before the manifest loses only that extent."""
        writer = self._writer(tmp_path)
        writer.add([Stretch(0, b"ok")], [])
        writer.close()
        assert writer.dump.path is not None
        with (writer.dump.path / DATA).open("ab") as f:
            f.write(b"half an extent")
        assert load_dump(writer.dump.path).stretches == [Stretch(0, b"ok")]
        again = DumpWriter(load_dump(writer.dump.path))
        again.add([Stretch(2, b"!")], [])
        assert load_dump(writer.dump.path).stretches == [Stretch(0, b"ok!")]

    def test_replace_swaps_one_extent(self, tmp_path):
        writer = self._writer(tmp_path)
        writer.add([Stretch(0, b"aaaa")], [], Extent(0, 3, "x"))
        writer.add([Stretch(4, b"bbbb")], [], Extent(4, 7, "y"))
        writer.replace(Extent(4, 7, "z", verified="first_read_error"), [Stretch(4, b"cccc")])
        assert writer.dump.path is not None
        back = load_dump(writer.dump.path)
        assert back.stretches == [Stretch(0, b"aaaacccc")]
        assert [(e.start, e.sha256) for e in back.extents] == [(0, "x"), (4, "z")]

    def test_a_version_1_dump_still_loads(self, tmp_path):
        path = tmp_path / "BCM" / "old"
        path.mkdir(parents=True)
        (path / "00001000-00001001.bin").write_bytes(b"\x01\x02")
        (path / MANIFEST).write_text(
            "manifest: 1\necu: BCM\ntx_id: '0x7A0'\nmemory_format: '24'\ntaken: x\n"
            "stretches:\n- {start: '0x00001000', end: '0x00001001', file: 00001000-00001001.bin}\n"
        )
        back = load_dump(path)
        assert back.stretches == [Stretch(0x1000, b"\x01\x02")] and back.complete
        assert not back.resumable

    def test_the_hash_does_not_depend_on_how_many_reads_brought_the_bytes(self):
        assert pieces_hash([Stretch(0, b"ab"), Stretch(2, b"c")]) == pieces_hash(
            [Stretch(0, b"abc")]
        )
        assert pieces_hash([Stretch(0, b"abc")]) != pieces_hash([Stretch(1, b"abc")])


class TestDiff:
    def test_runs(self):
        a = _dump([Stretch(0x1000, bytes([0, 1, 2, 3, 4, 5]))])
        b = _dump([Stretch(0x1000, bytes([0, 9, 9, 3, 4, 7])), Stretch(0x2000, b"z")])
        diff = diff_dumps(a, b)
        assert [(r.start, r.a, r.b) for r in diff.runs] == [
            (0x1001, b"\x01\x02", b"\x09\x09"),
            (0x1005, b"\x05", b"\x07"),
        ]
        assert diff.compared == 6 and diff.only_b == 1
        assert len(diff_dumps(a, b, min_run=2).runs) == 1

    def test_different_builds_are_refused(self):
        with pytest.raises(DumpError, match="software builds"):
            diff_dumps(_dump([], software="F195:1"), _dump([], software="F195:2"))


class TestExport:
    def test_ihex_keeps_holes_and_crosses_64k(self):
        text = to_ihex(_dump([Stretch(0xFFFE, b"\x01\x02\x03\x04"), Stretch(0x20000, b"\xaa")]))
        lines = text.splitlines()
        assert lines[0] == ":020000040000FA"
        assert ":020000040001F9" in lines and ":020000040002F8" in lines
        assert lines[-1] == ":00000001FF"
        for line in lines:  # every record's checksum makes its bytes sum to 0
            assert sum(bytes.fromhex(line[1:])) & 0xFF == 0

    def test_srec(self):
        lines = to_srec(_dump([Stretch(0x1000, b"\x01\x02")])).splitlines()
        assert lines[0].startswith("S0") and lines[-1].startswith("S7")
        assert lines[1] == "S30700001000010" + "2" + "E5"

    def test_bin_refuses_a_hole_unless_filled(self):
        dump = _dump([Stretch(0, b"\x01"), Stretch(3, b"\x02")])
        with pytest.raises(DumpError, match="hole"):
            to_bin(dump)
        assert to_bin(dump, fill=0xFF) == (0, b"\x01\xff\xff\x02")
