"""``canair memory dump`` over a fake ECU memory map (canair.live.memory.dump)."""

from __future__ import annotations

import asyncio

import pytest

from canair.ecus.memory import EcuMemory
from canair.live.memory.dump import mode_memory_dump, pace
from canair.live.memory.reads import BlockReader, MemoryTarget
from canair.memory.rate import RECOVER_AFTER
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


def _multi_byte_sizes(ecu):
    reads = (parse_memory_request(r, UDS) for r in ecu.sent if r.startswith("23"))
    return {r.size for r in reads if r is not None and r.size > 1}


def test_a_lost_reply_of_a_known_size_is_read_again_at_that_size(tmp_path):
    """The Ioniq's MCU loses a multi-frame reply now and then, at every size."""
    ecu = MemoryEcu([(0x0, 0xFFF, None)], lose={0x110: 1})
    run = _dump(ecu, [(0x100, 0x13F)], tmp_path, block=16, target=_target(max_block=16))
    assert (run.retries, run.halvings) == (1, 0)
    assert _multi_byte_sizes(ecu) == {16, 15}
    assert load_dump(run.dump.path).stretches[0].data == _expected(0x100, 0x13F)


def test_a_reply_lost_twice_halves_the_block(tmp_path):
    ecu = MemoryEcu([(0x0, 0xFFF, None)], lose={0x110: 2})
    run = _dump(ecu, [(0x100, 0x13F)], tmp_path, block=16, target=_target(max_block=16))
    assert (run.retries, run.halvings) == (1, 1)
    assert load_dump(run.dump.path).stretches[0].data == _expected(0x100, 0x13F)


def test_a_lost_reply_halves_at_once_when_no_size_is_known(tmp_path):
    ecu = MemoryEcu([(0x0, 0xFFF, None)], lose={0x110: 1})
    run = _dump(ecu, [(0x100, 0x13F)], tmp_path, block=16)
    assert (run.retries, run.halvings) == (0, 1)


def test_a_cut_short_reply_of_a_known_size_is_read_again(tmp_path):
    ecu = MemoryEcu([(0x0, 0xFFF, None)], drop_once={0x110})
    run = _dump(ecu, [(0x100, 0x13F)], tmp_path, block=16, target=_target(max_block=16))
    assert run.retries == 1
    assert _multi_byte_sizes(ecu) == {16, 15}
    assert load_dump(run.dump.path).stretches[0].data == _expected(0x100, 0x13F)


def _block_sizes(ecu):
    reads = (parse_memory_request(r, UDS) for r in ecu.sent if r.startswith("23"))
    return [r.size for r in reads if r is not None]


@pytest.mark.parametrize(
    ("ecu_args", "target"),
    [
        ({"lose": {0x110: 2}}, _target(max_block=16)),
        ({"drop_once": {0x110}}, _target()),
    ],
    ids=["lost-twice", "cut-short-unknown-size"],
)
def test_the_block_grows_back_after_a_halving(tmp_path, ecu_args, target):
    ecu = MemoryEcu([(0x0, 0xFFFF, None)], **ecu_args)
    run = _dump(ecu, [(0x100, 0x2FFF)], tmp_path, block=16, target=target)
    sizes = _block_sizes(ecu)
    halved = next(i for i, s in enumerate(sizes) if 1 < s <= 8)
    grown = next(i for i, s in enumerate(sizes) if i > halved and s > 8)
    assert grown - halved >= RECOVER_AFTER  # it stayed down until enough clean reads passed
    assert max(sizes) == 16 and 16 in sizes[grown:]
    assert load_dump(run.dump.path).stretches[0].data == _expected(0x100, 0x2FFF)


def test_the_block_never_goes_above_the_ceiling(tmp_path):
    ecu = MemoryEcu([(0x0, 0xFFFF, None)], lose={0x110: 2, 0x800: 2})
    run = _dump(ecu, [(0x100, 0x2FFF)], tmp_path, block=12, target=_target(max_block=16))
    assert max(_block_sizes(ecu)) == 12
    assert load_dump(run.dump.path).stretches[0].data == _expected(0x100, 0x2FFF)


def test_the_block_ceiling_is_the_ecus_max_block_without_block(tmp_path):
    ecu = MemoryEcu([(0x0, 0xFFFF, None)], lose={0x110: 2})
    _dump(ecu, [(0x100, 0x2FFF)], tmp_path, target=_target(max_block=20))
    assert max(_block_sizes(ecu)) == 20


def _paced(throttle_ms):
    ecu = MemoryEcu([])
    reader = BlockReader(ecu, _target(), UDS24, 16, throttle_s=throttle_ms / 1000)
    pace(reader, 16, throttle_ms)
    return reader


def test_errors_add_throttle_and_recovery_stops_at_the_one_asked_for():
    reader = _paced(50)
    for _ in range(2):
        reader.on_event("reread")
    assert reader.throttle_s == pytest.approx(0.07)
    for _ in range(3 * RECOVER_AFTER):
        reader.on_event("ok")
    assert reader.throttle_s == pytest.approx(0.05)


def test_no_throttle_asked_for_means_none_after_recovery():
    reader = _paced(0)
    for _ in range(2):
        reader.on_event("reread")
    assert reader.throttle_s > 0
    for _ in range(3 * RECOVER_AFTER):
        reader.on_event("ok")
    assert reader.throttle_s == 0


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


def test_a_range_that_refuses_by_silence_is_left_alone(tmp_path):
    ecu = MemoryEcu([(0x0, 0xFFFF, None)], silent_above=0xFFF)
    run = _dump(ecu, [(0x0, 0x1FFF)], tmp_path, block=64)
    dump = load_dump(run.dump.path)
    assert run.stopped is None and dump.complete
    assert dump.stretches[0].data == _expected(0x0, 0xFFF)
    assert (dump.gaps[-1].start, dump.gaps[-1].end, dump.gaps[-1].result) == (
        0x1000,
        0x1FFF,
        "no_answer",
    )


def test_a_killed_dump_keeps_what_it_read(tmp_path):
    from canair.memory.store import iter_dumps

    class Killed(MemoryEcu):
        def _memory(self, read):
            if self.memory_reads == 200:
                raise KeyboardInterrupt
            return super()._memory(read)

    with pytest.raises(KeyboardInterrupt):
        _dump(Killed([(0x0, 0xFFFF, None)]), [(0x0, 0xFFFF)], tmp_path, block=64)
    (dump,) = iter_dumps(tmp_path)
    assert not dump.complete and dump.extents
    assert dump.stretches[0].start == 0 and dump.bytes_read % 0x1000 == 0
    assert dump.stretches[0].data == _expected(0x0, dump.bytes_read - 1)
    assert dump.gaps[-1].result == "skipped" and dump.gaps[-1].end == 0xFFFF
