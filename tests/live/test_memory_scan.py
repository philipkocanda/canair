"""``canair scan memory`` over a fake ECU memory map (canair.live.memory.scan)."""

from __future__ import annotations

import asyncio

import pytest

from canair.ecus.memory import EcuMemory
from canair.live.memory.reads import MemoryTarget
from canair.live.memory.scan import mode_memory_scan
from canair.uds.memory import KWP_FORMAT, parse_format
from canair.uds.protocol import KWP, UDS
from tests.support.memory_ecu import MemoryEcu

UDS24 = parse_format("24")


def _target(fmt=UDS24, protocol=UDS) -> MemoryTarget:
    return MemoryTarget("BCM", 0x7A0, protocol, EcuMemory(fmt) if fmt else None, {})


def _scan(ecu, ranges, **kw):
    kw.setdefault("throttle_ms", 0)
    kw.setdefault("write_yaml", False)
    return asyncio.run(mode_memory_scan(ecu, kw.pop("target", _target()), ranges, **kw))


def test_boundaries_are_exact_after_bisection():
    ecu = MemoryEcu([(0x1234, 0x2ABC, None)])
    scan = _scan(ecu, [(0x0, 0x3FFF)], step=0x1000)
    assert [(r.start, r.end, r.result) for r in scan.regions] == [
        (0x0, 0x1233, "out_of_range"),
        (0x1234, 0x2ABC, "readable"),
        (0x2ABD, 0x3FFF, "out_of_range"),
    ]
    assert scan.stopped is None
    # Every read is one byte, except the block search inside the readable region.
    large = [r for r in ecu.sent if r.startswith("23") and not r.endswith("0001")]
    assert large and all(r.startswith("232400001234") for r in large)


def test_max_block_doubles_up_to_the_first_size_that_fails():
    ecu = MemoryEcu([(0x0, 0xFFFF, None)], max_block=200, too_large_nrc=None)
    scan = _scan(ecu, [(0x0, 0xFFFF)], step=0x4000, block_limit=1024)
    assert scan.max_block == 128
    sizes = [int(r[-4:], 16) for r in ecu.sent if r.startswith("23") and not r.endswith("0001")]
    assert sizes == [16, 32, 64, 128, 256]  # one failing size, never a larger one


def test_block_search_stays_under_the_limit():
    ecu = MemoryEcu([(0x0, 0xFFFF, None)])
    scan = _scan(ecu, [(0x0, 0xFFFF)], step=0x4000)
    assert scan.max_block == 128
    assert max(int(r[-4:], 16) for r in ecu.sent if r.startswith("23")) == 128


def test_an_ecu_that_hangs_on_a_large_read_stops_the_scan():
    ecu = MemoryEcu([(0x0, 0xFFFF, None)], max_block=100, too_large_nrc=None, hangs=True)
    scan = _scan(ecu, [(0x0, 0xFFFF)], step=0x4000)
    assert "stopped answering after a 128-byte read" in scan.stopped
    assert scan.regions and scan.regions[0].readable  # the map made before is kept


def test_locked_stops_the_scan():
    ecu = MemoryEcu([(0x0, 0x0FFF, None), (0x1000, 0xFFFF, 0x33)])
    scan = _scan(ecu, [(0x0, 0xFFFF)], step=0x800, find_block=False)
    assert scan.stopped and "securityAccessDenied" in scan.stopped
    assert scan.regions[-1].result == "locked"
    assert not any(r.startswith("27") for r in ecu.sent)  # never unlocks


def test_unsupported_stops_the_scan():
    ecu = MemoryEcu([(0x0, 0xFFFF, 0x11)])
    scan = _scan(ecu, [(0x0, 0xFFFF)])
    assert "does not support" in scan.stopped


def test_conditions_on_every_read_stop_the_scan():
    ecu = MemoryEcu([(0x0, 0xFFFFF, 0x22)])
    scan = _scan(ecu, [(0x0, 0xFFFFF)], step=0x100)
    assert "conditionsNotCorrect" in scan.stopped and scan.reads == 8


def test_format_probe_picks_the_one_that_reads():
    ecu = MemoryEcu([(0x0, 0xFF, None)])
    scan = _scan(ecu, [(0x0, 0xFF)], target=_target(fmt=None), step=0x80, find_block=False)
    assert scan.format == parse_format("44")


def test_format_probe_refuses_when_every_format_says_out_of_range():
    ecu = MemoryEcu([])
    scan = _scan(ecu, [(0x0, 0xFF)], target=_target(fmt=None))
    assert "pass --format" in scan.stopped and scan.regions == []


def test_kwp_ecu_needs_no_format():
    ecu = MemoryEcu([(0x0, 0x7FFF, None)], protocol=KWP)
    scan = _scan(ecu, [(0x0, 0xFFFF)], target=_target(KWP_FORMAT, KWP), find_block=False)
    assert scan.format == KWP_FORMAT
    assert all(len(r) == 10 for r in ecu.sent if r.startswith("23"))


def test_session_is_opened_when_asked():
    ecu = MemoryEcu([(0x0, 0xFF, None)])
    _scan(ecu, [(0x0, 0xFF)], session_mode="03", find_block=False)
    assert ecu.sessions == ["03"]


def test_map_is_written(tmp_path, monkeypatch):
    written = {}

    def fake_record(tx, regions, *, fmt=None, max_block=None):
        written.update(tx=tx, regions=regions, fmt=fmt, max_block=max_block)
        return tmp_path / "bcm.yaml"

    monkeypatch.setattr("canair.live.memory.scan.record_scan", fake_record)
    ecu = MemoryEcu([(0x0, 0xFF, None)], software="1.04")
    scan = _scan(ecu, [(0x0, 0x1FF)], step=0x100, write_yaml=True)
    assert scan.written == tmp_path / "bcm.yaml"
    assert written["tx"] == 0x7A0 and written["fmt"] == UDS24
    assert written["regions"][0].software == "F189:1.04"


@pytest.mark.parametrize("argv", [["scan", "memory", "BCM", "--range", "0-FF"]])
def test_confirmation_is_required_without_a_terminal(argv, monkeypatch, capsys):
    from canair.cli import main

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(argv) == 2
    assert "--yes" in capsys.readouterr().err
