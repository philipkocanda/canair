"""``canair memory image`` over a fake ECU (canair.live.memory.image): plan, read, pause, resume."""

from __future__ import annotations

import asyncio
import itertools

import pytest

from canair.cli.profile_context import active
from canair.ecus.memory import EcuMemory, MemoryRegion
from canair.live.memory.image import ImageOptions, mode_memory_image
from canair.live.memory.reads import MemoryTarget
from canair.memory.job import FULL, SAMPLE
from canair.memory.store import iter_dumps, load_dump
from canair.uds.memory import parse_format, parse_memory_request
from canair.uds.protocol import UDS
from tests.support.memory_ecu import MemoryEcu

UDS24 = parse_format("24")
SOFTWARE = "1.04"
PART = b"PN-1234"
ISLAND = 0x9400


def flash(address: int) -> int:
    """Programmed bytes in 0x0000-0x2FFF and a 64-byte island at 0x9000; erased elsewhere."""
    if 0x1000 <= address < 0x1000 + len(PART):
        return PART[address - 0x1000]
    if address <= 0x2FFF or ISLAND <= address < ISLAND + 0x40:
        return ((address * 2654435761) >> 13) & 0xFF
    return 0xFF


ECU_DEF = {
    "tx_id": 0x7A0,
    "can_bus": ["B-CAN"],
    "identity": {"part_number": "PN-1234", "vin": "KMHXXXXXXXXXXXXXX"},
    "pids": {"2101": {"status": "active", "parameters": {"X": {"expression": "B4"}}}},
}
PIDS = {"ecus": {"BCM": ECU_DEF, "IGPM": {"tx_id": 0x770, "can_bus": ["ALL"]}}}
REPLIES = {"2101": "6101000000000000", "1902FF": "5902FF"}


def _ecu(**kw) -> MemoryEcu:
    kw.setdefault("regions", [(0x0, 0xFFFF, None)])
    kw.setdefault("other_replies", dict(REPLIES))
    return MemoryEcu(software=SOFTWARE, data=flash, **kw)


def _target(max_block: int | None = 64, fmt=UDS24, regions=None) -> MemoryTarget:
    if regions is None:
        regions = (MemoryRegion(0, 0xFFFF, "readable", None, f"F189:{SOFTWARE}", "2026-11-01"),)
    return MemoryTarget("BCM", 0x7A0, UDS, EcuMemory(fmt, max_block, None, regions), ECU_DEF)


@pytest.fixture(autouse=True)
def recorded(monkeypatch, tmp_path):
    """Every map the image writes back lands here, never in a bundled profile."""
    calls: list[dict] = []

    def record(tx, regions, *, fmt=None, max_block=None, **_):
        calls.append({"tx": tx, "regions": regions, "fmt": fmt, "max_block": max_block})
        return tmp_path / "ecu.yaml"

    monkeypatch.setattr("canair.live.memory.image.record_scan", record)
    return calls


class FakeTime:
    """A clock that only moves when the run sleeps; ``on_sleep`` runs at each sleep."""

    def __init__(self, on_sleep=None, tick: float = 0.0):
        self.now = 0.0
        self.on_sleep = on_sleep
        self.tick = tick
        self.sleeps = 0

    def clock(self) -> float:
        self.now += self.tick
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.sleeps += 1
        if self.on_sleep is not None:
            self.on_sleep(self)


def _image(ecu, store, *, time=None, options=None, **kw):
    time = time or FakeTime()
    options = options or ImageOptions(health_every_s=1e9, wait_s=600, poll_s=30)
    return asyncio.run(
        mode_memory_image(
            ecu,
            kw.pop("target", _target()),
            store,
            options,
            pids_data=PIDS,
            profile=active(),
            sleep=time.sleep,
            clock=time.clock,
            **kw,
        )
    )


def _memory_reads(ecu):
    return [r for r in (parse_memory_request(s, UDS) for s in ecu.sent) if r is not None]


class TestSparseImage:
    def test_data_is_read_in_full_and_erased_space_sampled(self, tmp_path):
        ecu = _ecu()
        run = _image(ecu, tmp_path)
        assert run.stopped is None and run.saved is None and run.complete
        dump = load_dump(run.dump.path)
        got = dump.byte_map()
        assert all(got.get(a) == flash(a) for a in range(0x3000))
        assert all(got[a] == flash(a) for a in got)
        # The three data extents and the island's in full, twelve extents sampled.
        assert dump.bytes_read == 4 * 0x1000 + 12 * 2 * (64 + 63)
        kinds = {e.start: e.kind for e in dump.extents}
        assert kinds[0x0] == FULL and kinds[0x2000] == FULL and kinds[0x5000] == SAMPLE
        island = next(e for e in dump.extents if e.start == 0x9000)
        assert island.kind == FULL and "island" in island.events
        assert all(ISLAND + i in got for i in range(0x40))

    def test_the_report_checks(self, tmp_path):
        run = _image(_ecu(), tmp_path)
        assert run.identity == {"part_number": ("PN-1234", [0x1000])}
        assert {c.ecu for c in run.dtc_changes} == {"BCM", "IGPM"}
        assert not any(c.new or c.gone or c.unknown for c in run.dtc_changes)
        assert run.verdicts and set(run.verdicts) == {"same"}
        assert run.plan is not None and run.plan.flash_size == 0x10000 and run.plan.alias is None

    def test_only_reads_are_sent_and_the_block_stays_under_the_ceiling(self, tmp_path):
        ecu = _ecu()
        _image(ecu, tmp_path)
        allowed = {"22F189", "2101", "1902FF"}
        assert all(s.startswith("23") or s in allowed for s in ecu.sent)
        assert max(r.size for r in _memory_reads(ecu)) <= 64

    def test_the_expected_response_digit_prefers_61_byte_blocks(self, tmp_path):
        ecu = _ecu()
        run = _image(ecu, tmp_path, options=ImageOptions(digit=True, health_every_s=1e9))
        assert max(r.size for r in _memory_reads(ecu)) == 61
        assert run.plan is not None and run.plan.ceiling == 61

    def test_thorough_reads_every_byte_twice(self, tmp_path):
        ecu = _ecu()
        run = _image(ecu, tmp_path, options=ImageOptions(mode="thorough", health_every_s=1e9))
        dump = load_dump(run.dump.path)
        assert dump.bytes_read == 0x10000 and run.complete
        assert run.verdicts["same"] == 16


class TestPlanning:
    def test_plan_only_reads_no_extent(self, tmp_path):
        run = _image(_ecu(), tmp_path, options=ImageOptions(plan_only=True))
        assert run.plan is not None and run.dump is None and not run.declined
        assert list(iter_dumps(tmp_path)) == []

    def test_a_declined_plan_makes_no_job(self, tmp_path):
        run = _image(_ecu(), tmp_path, confirm_plan=lambda _run: False)
        assert run.declined and run.dump is None and list(iter_dumps(tmp_path)) == []

    def test_aliasing_reads_only_the_first_period(self, tmp_path):
        ecu = _ecu(alias=0x4000)
        run = _image(ecu, tmp_path, options=ImageOptions(plan_only=True))
        assert run.plan is not None and run.plan.alias == 0x4000
        assert (run.plan.start, run.plan.end) == (0, 0x3FFF)

    def test_a_map_past_a_silent_edge_grows_its_step(self, tmp_path):
        ecu = _ecu(silent_above=0xFFFF, regions=[(0, 0xFFFFF, None)])
        options = ImageOptions(ranges=((0, 0xFFFFF),), step=0x1000, plan_only=True)
        run = _image(ecu, tmp_path, options=options)
        assert run.stopped is None and run.plan is not None
        assert (run.plan.start, run.plan.end) == (0, 0xFFFF)
        above = [r for r in _memory_reads(ecu) if r.address > 0xFFFF]
        assert len(above) < 20

    def test_a_state_that_does_not_hold_stops_before_any_read(self, tmp_path):
        ecu = _ecu(other_replies={"1902FF": "5902FF"})  # the health PID does not answer
        run = _image(ecu, tmp_path)
        assert run.stopped and "health read 2101 failed" in run.stopped
        assert _memory_reads(ecu) == []


def _byte_maps_equal(a, b):
    return load_dump(a.dump.path).byte_map() == load_dump(b.dump.path).byte_map()


class TestResume:
    def test_a_job_stopped_mid_way_resumes_to_the_same_bytes(self, tmp_path):
        whole = _image(_ecu(), tmp_path / "whole")
        calls = {"n": 0}

        def skip():
            calls["n"] += 1
            return calls["n"] > 300

        first = _image(_ecu(), tmp_path / "job", skip=skip)
        assert first.saved == "stopped by you" and first.dump.resumable
        dump = load_dump(first.dump.path)
        assert 0 < len(dump.extents) < 16
        again = _image(_ecu(), tmp_path / "job", resume=dump)
        assert again.complete and again.resumed
        assert _byte_maps_equal(whole, again)

    def test_a_killed_run_keeps_its_extents_and_resumes(self, tmp_path):
        whole = _image(_ecu(), tmp_path / "whole")

        class Killed(MemoryEcu):
            def _memory(self, read):
                if self.memory_reads == 150:
                    raise KeyboardInterrupt
                return super()._memory(read)

        killed = Killed(
            regions=[(0, 0xFFFF, None)], software=SOFTWARE, data=flash, other_replies=REPLIES
        )
        with pytest.raises(KeyboardInterrupt):
            _image(killed, tmp_path / "job")
        (dump,) = iter_dumps(tmp_path / "job")
        assert dump.resumable and dump.extents
        again = _image(_ecu(), tmp_path / "job", resume=dump)
        assert again.complete and _byte_maps_equal(whole, again)

    def test_a_resume_never_starts_above_the_block_it_had_reached(self, tmp_path):
        first = _image(_ecu(), tmp_path, skip=iter([False] * 30 + [True] * 999).__next__)
        dump = load_dump(first.dump.path)
        dump.rate = {"block": 20, "throttle_ms": 0}
        ecu = _ecu()
        _image(ecu, tmp_path, resume=dump)
        # The rate may climb again after many clean reads, but it starts where it was.
        assert max(r.size for r in _memory_reads(ecu)[:400]) <= 20

    def test_a_reflashed_ecu_is_refused(self, tmp_path):
        first = _image(_ecu(), tmp_path, skip=iter([False] * 30 + [True] * 999).__next__)
        ecu = _ecu()
        ecu.software = "1.05"
        again = _image(ecu, tmp_path, resume=load_dump(first.dump.path))
        assert again.stopped and "reflashed" in again.stopped
        assert _memory_reads(ecu) == []

    def test_a_plain_dump_cannot_resume(self, tmp_path):
        first = _image(_ecu(), tmp_path)
        dump = load_dump(first.dump.path)
        dump.plan = None
        with pytest.raises(ValueError, match="plain dump"):
            _image(_ecu(), tmp_path, resume=dump)


class TestPauses:
    def test_an_ecu_that_goes_silent_pauses_and_resumes_in_the_run(self, tmp_path):
        whole = _image(_ecu(), tmp_path / "whole")
        ecu = _ecu(offline_after=200)

        def back(time):
            if time.sleeps == 2:
                ecu.offline = False

        run = _image(ecu, tmp_path / "job", time=FakeTime(back))
        assert run.complete and _byte_maps_equal(whole, run)
        (pause,) = load_dump(run.dump.path).pauses
        assert "stopped answering" in pause["reason"] and pause["how"] == "resumed in the run"

    def test_the_wait_polls_gently(self, tmp_path):
        ecu = _ecu(offline_after=200)
        time = FakeTime()
        options = ImageOptions(health_every_s=1e9, wait_s=300, poll_s=30)
        before = []

        def count(_t):
            before.append(len(ecu.sent))

        time.on_sleep = count
        run = _image(ecu, tmp_path, time=time, options=options)
        assert run.saved and run.dump.resumable
        assert time.sleeps == 10
        polls = [b - a for a, b in itertools.pairwise(before)]
        assert all(n == 1 for n in polls)  # one 1-byte read per poll, nothing more

    def test_a_job_that_did_not_resume_is_resumed_later(self, tmp_path):
        whole = _image(_ecu(), tmp_path / "whole")
        options = ImageOptions(health_every_s=1e9, wait_s=60, poll_s=30)
        first = _image(_ecu(offline_after=300), tmp_path / "job", options=options)
        assert first.saved and first.dump.pauses[0]["how"] == "saved for --resume"
        again = _image(_ecu(), tmp_path / "job", resume=load_dump(first.dump.path))
        assert again.complete and _byte_maps_equal(whole, again)

    def test_a_failed_health_check_pauses_until_it_passes(self, tmp_path):
        class Sick(MemoryEcu):
            def _memory(self, read):
                if self.memory_reads == 100:
                    self.other_replies.pop("2101", None)
                return super()._memory(read)

        ecu = Sick(
            regions=[(0, 0xFFFF, None)], software=SOFTWARE, data=flash, other_replies=dict(REPLIES)
        )

        def heal(_time):
            ecu.other_replies["2101"] = REPLIES["2101"]

        options = ImageOptions(health_every_s=5, wait_s=600, poll_s=30)
        run = _image(ecu, tmp_path, time=FakeTime(heal, tick=1.0), options=options)
        assert run.complete
        pauses = load_dump(run.dump.path).pauses
        assert pauses and "health read 2101 failed" in pauses[0]["reason"]


class TestVerify:
    def test_a_volatile_range_stops_the_job(self, tmp_path):
        ecu = _ecu(volatile={0x10})
        run = _image(ecu, tmp_path)
        assert run.stopped and "three different ways" in run.stopped
        assert run.verdicts["volatile"] == 1


class TestCommand:
    def test_confirmation_is_required_without_a_terminal(self, monkeypatch, capsys):
        from canair.cli import main

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        assert main(["memory", "image", "BCM"]) == 2
        assert "--yes" in capsys.readouterr().err

    def test_an_ecu_or_a_resume_is_required(self, capsys):
        from canair.cli import main

        with pytest.raises(SystemExit) as stop:
            main(["memory", "image", "--yes"])
        assert stop.value.code == 2
        assert "name an ECU, or pass --resume" in capsys.readouterr().err

    def test_a_resumable_job_is_listed_as_such(self, tmp_path, capsys):
        from canair.cli.commands.memory.render import print_listing

        first = _image(_ecu(), tmp_path, skip=iter([False] * 30 + [True] * 999).__next__)
        print_listing([load_dump(first.dump.path)], as_json=False)
        assert "(resumable)" in capsys.readouterr().out


class TestLinkDrops:
    def test_a_drop_without_a_reconnect_saves_the_job_at_once(self, tmp_path):
        class Drops(MemoryEcu):
            def _memory(self, read):
                if self.memory_reads == 150:
                    raise ConnectionResetError("peer closed")
                return super()._memory(read)

        ecu = Drops(
            regions=[(0, 0xFFFF, None)], software=SOFTWARE, data=flash, other_replies=REPLIES
        )
        time = FakeTime()
        run = _image(ecu, tmp_path, time=time)
        assert run.saved and "link dropped" in run.saved and run.dump.resumable
        assert time.sleeps == 0

    def test_a_drop_is_re_homed_by_the_reconnector(self, tmp_path):
        whole = _image(_ecu(), tmp_path / "whole")

        class Drops(MemoryEcu):
            def _memory(self, read):
                if self.memory_reads == 150:
                    raise ConnectionResetError("peer closed")
                return super()._memory(read)

        dropping = Drops(
            regions=[(0, 0xFFFF, None)], software=SOFTWARE, data=flash, other_replies=REPLIES
        )
        fresh = _ecu()

        async def reconnect(link, _steps, *, notice=None, stop=None):
            await link.close_client()
            link.rebind(fresh)
            return True

        run = _image(dropping, tmp_path / "job", reconnect=reconnect)
        assert run.complete and _byte_maps_equal(whole, run)
        assert fresh.memory_reads > 0
        (pause,) = load_dump(run.dump.path).pauses
        assert pause["how"] == "resumed in the run"


def test_a_reflash_during_a_pause_stops_the_job(tmp_path):
    ecu = _ecu(offline_after=200)

    def reflash(_time):
        ecu.offline = False
        ecu.software = "1.05"

    run = _image(ecu, tmp_path, time=FakeTime(reflash))
    assert run.stopped and "reflashed" in run.stopped
    assert not run.complete


def test_the_required_state_is_not_taken_for_a_capture_flag():
    """`--state` here names a state the run needs; the runtime refuses capture flags."""
    from canair.cli import build_parser
    from canair.cli.live.runtime import saving

    args = build_parser().parse_args(["memory", "image", "VCU", "--state", "CHARGING"])
    assert args.required_state == "CHARGING"
    assert not saving(args)


class TestDefaultSpace:
    """With no region recorded, the image maps the format's whole space and records it."""

    UDS13 = parse_format("13")  # a 3-byte address: a 16 MiB space

    def test_no_recorded_region_maps_the_whole_space_and_records_it(self, tmp_path, recorded):
        ecu = _ecu(silent_above=0xFFFF, regions=[(0, 0xFFFFFF, None)], max_block=100)
        target = _target(max_block=None, fmt=self.UDS13, regions=())
        run = _image(ecu, tmp_path, target=target, options=ImageOptions(plan_only=True))
        assert run.stopped is None and run.plan is not None
        assert (run.plan.start, run.plan.end) == (0, 0xFFFF)
        probes = [r for r in _memory_reads(ecu) if r.size == 1 and r.address % 0x10000 == 0]
        assert len({r.address for r in probes}) <= 257  # about 256 probes, 64 KiB apart
        (write,) = recorded
        assert write["fmt"] == self.UDS13 and write["max_block"] == 64  # 128 failed
        first = write["regions"][0]
        assert (first.start, first.end, first.result) == (0, 0xFFFF, "readable")
        assert run.plan.ceiling == 64 and run.written == tmp_path / "ecu.yaml"

    def test_a_region_of_another_software_build_is_mapped_again(self, tmp_path, recorded):
        old = (MemoryRegion(0, 0xFFF, "readable", None, "F189:0.99", "2026-01-01"),)
        ecu = _ecu(silent_above=0xFFFF, regions=[(0, 0xFFFFFF, None)])
        target = _target(fmt=self.UDS13, regions=old)
        run = _image(ecu, tmp_path, target=target, options=ImageOptions(plan_only=True))
        assert run.plan is not None and run.plan.end == 0xFFFF
        assert recorded and recorded[0]["max_block"] is None  # max_block was recorded

    def test_dry_run_writes_nothing(self, tmp_path, recorded):
        ecu = _ecu(silent_above=0xFFFF, regions=[(0, 0xFFFFFF, None)])
        target = _target(max_block=None, fmt=self.UDS13, regions=())
        options = ImageOptions(plan_only=True, dry_run=True)
        run = _image(ecu, tmp_path, target=target, options=options)
        assert run.plan is not None and recorded == [] and run.written is None

    def test_a_four_byte_space_needs_a_range(self, tmp_path, recorded):
        ecu = _ecu()
        run = _image(
            ecu, tmp_path, target=_target(regions=()), options=ImageOptions(plan_only=True)
        )
        assert run.stopped and "pass --range" in run.stopped
        assert _memory_reads(ecu) == [] and recorded == []

    def test_an_ecu_that_answers_its_whole_space_stops_after_the_map(self, tmp_path, recorded):
        ecu = _ecu(regions=[(0, 0xFFFFFF, None)])
        target = _target(max_block=None, fmt=self.UDS13, regions=())
        run = _image(ecu, tmp_path, target=target, options=ImageOptions(plan_only=True))
        assert run.stopped and "does not check the range" in run.stopped
        assert "--range" in run.stopped and run.plan is None
        assert {r.size for r in _memory_reads(ecu)} == {1}  # the map's probes, nothing larger
        assert recorded == []

    def test_a_recorded_whole_space_region_stops_before_any_read(self, tmp_path, recorded):
        everywhere = (MemoryRegion(0, 0xFFFFFF, "readable", None, f"F189:{SOFTWARE}", "x"),)
        ecu = _ecu(regions=[(0, 0xFFFFFF, None)])
        target = _target(fmt=self.UDS13, regions=everywhere)
        run = _image(ecu, tmp_path, target=target, options=ImageOptions(plan_only=True))
        assert run.stopped and "does not check the range" in run.stopped
        assert _memory_reads(ecu) == [] and recorded == []

    def test_a_named_range_that_answers_whole_is_read(self, tmp_path, recorded):
        ecu = _ecu(regions=[(0, 0xFFFFFF, None)])
        target = _target(max_block=None, fmt=self.UDS13, regions=())
        options = ImageOptions(ranges=((0, 0xFFFF),), plan_only=True)
        run = _image(ecu, tmp_path, target=target, options=options)
        assert run.stopped is None and run.plan is not None
        assert (run.plan.start, run.plan.end) == (0, 0xFFFF)
        assert all(r.address <= 0xFFFF for r in _memory_reads(ecu))

    def test_a_recorded_map_without_a_block_still_searches_it(self, tmp_path, recorded):
        ecu = _ecu(max_block=40)
        run = _image(
            ecu, tmp_path, target=_target(max_block=None), options=ImageOptions(plan_only=True)
        )
        assert run.plan is not None and run.plan.ceiling == 32
        assert recorded == [{"tx": 0x7A0, "regions": [], "fmt": UDS24, "max_block": 32}]
