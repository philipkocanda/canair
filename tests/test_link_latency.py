"""Link round-trip estimation and the resync window it sizes.

The bug these guard: a drain window tuned for a LAN is shorter than a cellular
reply is old, so the reply survives the drain and the offset it caused becomes
permanent. The window must therefore come from *measurement*, and the
measurement must be of the link alone.
"""

from __future__ import annotations

import pytest

from canlib.link_latency import _K, _MIN_SAMPLES, LinkLatency
from canlib.transport.elm327_terminal import _LINK_LATENCY_MARGIN, _RESYNC_QUIET_MAX
from tests.test_terminal import _term_prog


class TestNoOpinionUntilMeasured:
    def test_a_fresh_estimator_has_no_opinion(self):
        # None is not zero: a caller must fall back to its own default, not to a
        # window of no length at all.
        link = LinkLatency()
        assert link.rtt is None
        assert link.budget is None

    def test_one_sample_is_not_yet_an_estimate(self):
        # A single measurement cannot tell a slow link from a scheduling hiccup.
        link = LinkLatency()
        link.observe(0.4)
        assert link.budget is None

    def test_the_estimate_appears_at_the_sample_floor(self):
        link = LinkLatency()
        for _ in range(_MIN_SAMPLES):
            link.observe(0.4)
        assert link.rtt == pytest.approx(0.4, abs=0.05)
        assert link.budget is not None

    def test_the_floor_is_returned_until_then(self):
        link = LinkLatency()
        link.observe(9.0)
        assert link.allowance(0.5) == 0.5


class TestEstimate:
    def test_a_steady_link_converges_on_its_round_trip(self):
        link = LinkLatency()
        for _ in range(40):
            link.observe(0.25)
        assert link.rtt == pytest.approx(0.25, abs=0.01)
        # No jitter, so the budget collapses onto the round trip itself.
        assert link.budget == pytest.approx(0.25, abs=0.02)

    def test_jitter_widens_the_budget_beyond_the_mean(self):
        # The whole reason for RFC 6298 over a plain mean: on a jittery link the
        # mean is an underestimate about half the time, and every underestimate
        # orphans another reply.
        steady, jittery = LinkLatency(), LinkLatency()
        for _ in range(40):
            steady.observe(0.5)
        for i in range(40):
            jittery.observe(0.1 if i % 2 else 0.9)
        assert jittery.rtt == pytest.approx(0.5, abs=0.2)
        assert jittery.budget > steady.budget * 2

    def test_the_budget_is_the_rfc_6298_formula(self):
        link = LinkLatency()
        for _ in range(30):
            link.observe(0.3)
        assert link.budget == pytest.approx(link.srtt + _K * link.rttvar)

    def test_it_tracks_a_link_that_gets_slower(self):
        # Latency changes while driving; a fixed constant cannot follow it.
        link = LinkLatency()
        for _ in range(20):
            link.observe(0.05)
        fast = link.budget
        for _ in range(60):
            link.observe(1.5)
        assert link.budget > fast * 5

    def test_allowance_never_drops_below_the_floor(self):
        # A fast link must not be given a tighter window than the one already
        # known to work.
        link = LinkLatency()
        for _ in range(30):
            link.observe(0.001)
        assert link.allowance(0.5) == 0.5

    def test_allowance_follows_a_slow_link_above_the_floor(self):
        link = LinkLatency()
        for _ in range(30):
            link.observe(2.0)
        assert link.allowance(0.5) > 2.0


class TestBadSamples:
    @pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), float("nan")])
    def test_impossible_samples_are_ignored_not_clamped(self, bad):
        # They mean the caller's bookkeeping is wrong; a zero would drag a real
        # estimate down instead of being visibly absent.
        link = LinkLatency()
        for _ in range(_MIN_SAMPLES):
            link.observe(0.4)
        before = link.budget
        link.observe(bad)
        assert link.budget == before

    def test_a_bad_first_sample_leaves_no_opinion(self):
        link = LinkLatency()
        link.observe(float("nan"))
        assert link.rtt is None
        assert link.snapshot() is None


class TestSnapshot:
    def test_snapshot_reports_milliseconds(self):
        link = LinkLatency()
        for _ in range(10):
            link.observe(0.25)
        snap = link.snapshot()
        assert snap is not None
        assert snap["n"] == 10
        assert snap["rtt_ms"] == pytest.approx(250.0, abs=10.0)


class TestTerminalMeasuresTheLinkNotTheCar:
    """Only commands the adapter answers by itself may feed the estimate."""

    @pytest.mark.asyncio
    async def test_a_uds_read_is_not_a_link_sample(self):
        # A UDS round trip is link + ECU think time and cannot be separated after
        # the fact, so feeding it in would inflate the estimate with the car's time.
        t = _term_prog(["6101AA\r>"] * 6)
        for _ in range(6):
            await t.send_command("2101")
        assert t.link.rtt is None

    @pytest.mark.asyncio
    async def test_a_keepalive_is_not_a_link_sample(self):
        t = _term_prog(["7E00\r>"] * 6)
        for _ in range(6):
            await t.send_command("3E00")
        assert t.link.rtt is None

    @pytest.mark.asyncio
    async def test_a_header_write_is_a_link_sample(self):
        # ATSH never reaches the bus, and one pair is sent per ECU switch, so this
        # is what keeps the estimate current on the hot path.
        t = _term_prog(["OK\r>"] * 6)
        for _ in range(6):
            await t.send_command("ATSH7E4")
        assert t.link.rtt is not None

    @pytest.mark.asyncio
    async def test_a_chip_reset_is_not_a_link_sample(self):
        # ATZ is seconds of adapter work; counting it would claim the network is
        # slow when it is not.
        t = _term_prog(["ELM327 v1.5\r>"] * 6)
        for _ in range(6):
            await t.send_command("ATZ")
        assert t.link.rtt is None

    @pytest.mark.asyncio
    async def test_a_timed_out_command_is_not_a_link_sample(self):
        # Its elapsed time is the deadline, which says nothing about the link.
        t = _term_prog([None])
        t.timeout = 0.05
        await t.send_command("ATSH7E4")
        assert t.link.n == 0


class TestMeasurementSizesTheResyncWindow:
    @staticmethod
    def _drain_spy(t):
        calls = []

        async def drain(per_recv_timeout=0.2, max_seconds=1.0):
            calls.append({"per_recv_timeout": per_recv_timeout, "max_seconds": max_seconds})

        t._channel.drain = drain
        return calls

    @pytest.mark.asyncio
    async def test_an_unmeasured_link_uses_the_floor(self):
        t = _term_prog(["ELM327 v1.5\r>", "6101AA\r>"])
        calls = self._drain_spy(t)
        t.elm_timeout_cmd = "ATST96"  # 0x96 * 4.096ms = 614ms
        t._pipe_dirty = True
        await t.send_command("2101")
        assert calls[0]["per_recv_timeout"] == pytest.approx(0.614 + _LINK_LATENCY_MARGIN, abs=0.01)

    @pytest.mark.asyncio
    async def test_a_slow_link_widens_the_window(self):
        # The reported failure: the reply was older than the window was wide.
        t = _term_prog(["ELM327 v1.5\r>", "6101AA\r>"])
        calls = self._drain_spy(t)
        t.elm_timeout_cmd = "ATST96"
        for _ in range(30):
            t.link.observe(1.2)
        t._pipe_dirty = True
        await t.send_command("2101")
        assert calls[0]["per_recv_timeout"] > 1.8
        assert calls[0]["per_recv_timeout"] <= _RESYNC_QUIET_MAX

    @pytest.mark.asyncio
    async def test_a_fast_link_is_not_given_a_tighter_window(self):
        t = _term_prog(["ELM327 v1.5\r>", "6101AA\r>"])
        calls = self._drain_spy(t)
        t.elm_timeout_cmd = "ATST96"
        for _ in range(30):
            t.link.observe(0.0005)  # LAN
        t._pipe_dirty = True
        await t.send_command("2101")
        assert calls[0]["per_recv_timeout"] == pytest.approx(0.614 + _LINK_LATENCY_MARGIN, abs=0.01)


class TestSeedFromAnUnambiguousMeasurement:
    """A TCP handshake is one round trip by construction — one is enough."""

    def test_a_single_seed_yields_an_estimate(self):
        link = LinkLatency()
        link.seed(0.4)
        assert link.rtt == 0.4
        assert link.budget == pytest.approx(0.4 + _K * 0.2)

    def test_a_seed_is_available_before_any_protocol_traffic(self):
        # This is the whole point: the ISO-TP budgets are chosen at stack
        # construction, before there is a single exchange to learn from.
        link = LinkLatency()
        assert link.budget is None
        link.seed(1.0)
        assert link.budget is not None

    def test_observations_refine_a_seed(self):
        link = LinkLatency()
        link.seed(1.0)
        for _ in range(40):
            link.observe(0.1)
        assert link.rtt is not None
        assert link.rtt < 0.3

    def test_a_bad_seed_is_ignored(self):
        link = LinkLatency()
        for bad in (0.0, -1.0, float("inf"), float("nan")):
            link.seed(bad)
        assert link.snapshot() is None

    def test_a_seed_does_not_discard_existing_samples(self):
        link = LinkLatency()
        for _ in range(5):
            link.observe(0.2)
        link.seed(0.5)
        assert link.n == 5
        assert link.rtt == 0.5
