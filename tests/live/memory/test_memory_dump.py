"""``canair memory dump`` over a fake ECU memory map (canair.live.memory.dump)."""

from __future__ import annotations

import asyncio

import pytest

from canair.ecus.memory import EcuMemory
from canair.live.memory.dump import mode_memory_dump
from canair.live.memory.reads import MemoryTarget
from canair.memory.store import load_dump
from canair.uds.memory import parse_format, parse_memory_request
from canair.uds.protocol import UDS
from tests.support.memory_ecu import MemoryEcu

UDS24 = parse_format("24")


def _target(max_block=None) -> MemoryTarget:
    return MemoryTarget("BCM", 0x7A0, UDS, EcuMemory(UDS24, max_block), {})


def _dump(ecu, ranges, store, **kw):
    target = kw.pop("target", _target())
    return asyncio.run(mode_memory_dump(ecu, target, ranges, store, **kw))


def _expected(start, end):
    return bytes(a & 0xFF for a in range(start, end + 1))


def test_block_sizes_alternate_and_bytes_are_exact(tmp_path):
    ecu = MemoryEcu([(0x0, 0xFFF, None)])
    run = _dump(ecu, [(0x100, 0x1FF)], tmp_path, block=16)
    sizes = [parse_memory_request(r, UDS).size for r in ecu.sent if r.startswith("23")]
    assert sizes[:4] == [16, 15, 16, 15]
    dump = load_dump(run.dump.path)
    assert len(dump.stretches) == 1 and dump.stretches[0].data == _expected(0x100, 0x1FF)
    assert dump.complete and dump.gaps == []


def test_a_stale_reply_is_caught_and_the_block_read_again(tmp_path):
    ecu = MemoryEcu([(0x0, 0xFFF, None)], stale_once={0x110})
    run = _dump(ecu, [(0x100, 0x13F)], tmp_path, block=16)
    assert run.rereads == 1
    assert load_dump(run.dump.path).stretches[0].data == _expected(0x100, 0x13F)


def test_holes_stay_holes(tmp_path):
    ecu = MemoryEcu([(0x0, 0x7F, None), (0x80, 0xBF, 0x31), (0xC0, 0xFF, None)])
    run = _dump(ecu, [(0x0, 0xFF)], tmp_path, block=0x40)
    dump = load_dump(run.dump.path)
    assert [(s.start, s.end) for s in dump.stretches] == [(0x0, 0x7F), (0xC0, 0xFF)]
    assert (dump.gaps[0].result, dump.gaps[0].nrc) == ("out_of_range", 0x31)
    assert all(0x80 <= g.start and g.end <= 0xBF for g in dump.gaps)


def test_a_block_too_large_is_halved(tmp_path):
    ecu = MemoryEcu([(0x0, 0xFFF, None)], max_block=20, too_large_nrc=None)
    run = _dump(ecu, [(0x0, 0xFF)], tmp_path, block=64)
    assert run.halvings >= 1
    assert load_dump(run.dump.path).stretches[0].data == _expected(0x0, 0xFF)


def test_locked_is_left_alone_for_the_rest_of_the_range(tmp_path):
    ecu = MemoryEcu([(0x0, 0x3F, None), (0x40, 0xFFFF, 0x33)])
    run = _dump(ecu, [(0x0, 0xFFF)], tmp_path, block=0x20)
    dump = load_dump(run.dump.path)
    assert dump.gaps[-1].result == "locked" and dump.gaps[-1].end == 0xFFF
    assert dump.stretches[0].end == 0x3F  # the edge is exact
    addresses = [parse_memory_request(r, UDS).address for r in ecu.sent if r.startswith("23")]
    assert max(addresses) == 0x40  # nothing past the first locked address is asked


def test_skip_keeps_what_was_read(tmp_path):
    ecu = MemoryEcu([(0x0, 0xFFF, None)])
    calls = iter([False, False, True])
    run = _dump(ecu, [(0x0, 0xFF), (0x200, 0x2FF)], tmp_path, block=16, skip=lambda: next(calls))
    dump = load_dump(run.dump.path)
    assert run.stopped == "skipped" and not dump.complete
    assert dump.bytes_read == 31
    assert [g.result for g in dump.gaps] == ["skipped", "skipped"]


def test_a_silent_ecu_ends_the_dump(tmp_path):
    ecu = MemoryEcu([(0x0, 0xFF, None)], max_block=0, too_large_nrc=None)
    run = _dump(ecu, [(0x0, 0xFF)], tmp_path, block=4)
    assert run.stopped == "the ECU stopped answering"
    assert load_dump(run.dump.path).gaps[-1].result == "not_read"


def test_needs_a_format(tmp_path):
    target = MemoryTarget("BCM", 0x7A0, UDS, None, {})
    with pytest.raises(ValueError, match="no memory format"):
        _dump(MemoryEcu([]), [(0, 1)], tmp_path, target=target)


def test_dumps_never_land_in_the_profile(tmp_path, monkeypatch):
    from canair.memory.store import store_root

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    root = store_root("some-profile")
    assert "profiles" not in root.parts and root.is_relative_to(tmp_path / "cfg")


def test_confirmation_is_required_without_a_terminal(monkeypatch, capsys):
    from canair.cli import main

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["memory", "dump", "BCM", "0-FF"]) == 2
    assert "--yes" in capsys.readouterr().err


def test_a_block_above_the_known_max_block_is_refused(tmp_path):
    with pytest.raises(ValueError, match="max_block 64"):
        _dump(MemoryEcu([]), [(0, 0xFF)], tmp_path, block=128, target=_target(max_block=64))
