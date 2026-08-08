"""Live link round-trip estimation — how long the *network* takes, not the car.

canair's timeouts have to answer one recurring question: *how long must it stay
quiet before a reply is provably not coming?* Two very different delays add up to
that answer:

- the **ECU's** think time, which the profile states (``response_timeout_ms`` →
  ``ATST``, see :mod:`canlib.timeouts`), and
- the **link's** round trip, which nobody can state up front. It is 0.3 ms on a
  LAN and 2 s on a phone hotspot behind a VPN, it changes while driving, and a
  user cannot reasonably be asked to configure it.

Every budget that was tuned for a LAN and then met a cellular link has failed the
same way: the deadline expired while the answer was still in flight, the late
answer arrived during the *next* exchange, and it was served as that exchange's
result. So the link term must be **measured**, and this is where the measurement
lives — a primitive small enough to sit on the hot path, shared by the ELM327
engine and the raw ISO-TP client rather than re-derived in each.

**Measure the link alone.** A UDS round trip is link + ECU and cannot be
separated after the fact, so feeding one in would inflate the estimate with the
car's think time. Feed it exchanges the *adapter* answers by itself instead — an
``AT`` command never reaches the CAN bus, so its round trip is link latency plus
microseconds of parsing.

**Why RFC 6298 and not a plain mean.** This is the same problem TCP solves when
sizing a retransmission timeout, and it has the same trap: on a jittery link the
mean is an *under*estimate roughly half the time, and every underestimate creates
another orphaned reply. TCP's answer is a smoothed estimate plus four smoothed
deviations, which widens automatically as jitter rises. Reusing it means the
behaviour under load is understood rather than invented.
"""

from __future__ import annotations

import math

# RFC 6298 smoothing gains: `alpha` on the estimate, `beta` on the deviation.
_ALPHA = 1.0 / 8.0
_BETA = 1.0 / 4.0

# Deviation multiplier from RFC 6298's RTO formula (`srtt + K * rttvar`). Four
# smoothed deviations is roughly a 95% upper bound for the jitter seen so far.
_K = 4.0

# Samples needed before `budget` is offered. One sample cannot distinguish the
# link's latency from a one-off scheduling hiccup, and an early overestimate is
# just as harmful as an underestimate: it slows every recovery.
_MIN_SAMPLES = 3


class LinkLatency:
    """Smoothed round-trip estimate for one connection.

    Attached to a request client as ``.link``. :meth:`observe` is a handful of
    float operations, cheap enough for the hot path; :attr:`budget` is what
    callers size a wait on.
    """

    __slots__ = ("n", "rttvar", "srtt")

    def __init__(self) -> None:
        self.srtt: float | None = None
        self.rttvar = 0.0
        self.n = 0

    def observe(self, rtt: float) -> None:
        """Record one **link-only** round trip, in seconds.

        Non-finite and non-positive samples are ignored rather than clamped: they
        mean the caller's clock or bookkeeping is wrong, and a zero would drag a
        real estimate down.
        """
        if not math.isfinite(rtt) or rtt <= 0.0:
            return
        self.n += 1
        if self.srtt is None:
            self.srtt = rtt
            self.rttvar = rtt / 2.0
            return
        self.rttvar = (1.0 - _BETA) * self.rttvar + _BETA * abs(self.srtt - rtt)
        self.srtt = (1.0 - _ALPHA) * self.srtt + _ALPHA * rtt

    @property
    def rtt(self) -> float | None:
        """The smoothed round trip, or ``None`` before enough samples."""
        return self.srtt if self.n >= _MIN_SAMPLES else None

    @property
    def budget(self) -> float | None:
        """How long to wait for something one round trip away, or ``None``.

        ``None`` means "no opinion yet" — a caller must fall back to its own
        default rather than treat it as zero.
        """
        if self.srtt is None or self.n < _MIN_SAMPLES:
            return None
        return self.srtt + _K * self.rttvar

    def allowance(self, floor: float) -> float:
        """:attr:`budget` but never below ``floor`` — the value callers want.

        Keeps the caller's hardcoded default as a *floor* rather than replacing
        it, so a fast link is never given a tighter window than the one that was
        known to work, and a slow one is given the room it demonstrably needs.
        """
        measured = self.budget
        return max(floor, measured) if measured is not None else floor

    def snapshot(self) -> dict[str, float | int] | None:
        """Milliseconds, for provenance and the monitor's health line."""
        if self.srtt is None:
            return None
        budget = self.budget
        return {
            "n": self.n,
            "rtt_ms": round(self.srtt * 1000.0, 1),
            "jitter_ms": round(self.rttvar * 1000.0, 1),
            "budget_ms": round(budget * 1000.0, 1) if budget is not None else 0.0,
        }
