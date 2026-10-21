"""Spot captures that hold another ECU's reply to the same PID.

Two ECUs often answer the same request with the same header. On the Ioniq, ``21F2`` is answered
``61 F2 …`` by the BMS, MCU and VCU alike. A reply that was still queued from one ECU and arrived
in the next ECU's request slot therefore passes the echo check and is saved under the wrong
``rx``. See ``plans/2026-10-21-cross-ecu-reply-misfile.md``.

Nothing in a single capture can prove where it came from, so this works across the corpus. A
capture is suspect when:

- **its length** breaks its own ECU's established length for that PID but matches another ECU's
  established length for the same PID, or
- **its bytes** are identical to a reply recorded under another ECU for the same PID, which has
  that payload at least as often.

Negative responses (``7F …``) are left out: two ECUs refusing a request look alike by design.
Pure and device-free, so ``validate captures`` and any future capture tool can share it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

# A length is an ECU's "own" for a PID only once it is seen often and almost exclusively, so
# variable-length PIDs and thinly sampled ones never produce a length finding.
MIN_SIGNATURE_SAMPLES = 5
MIN_SIGNATURE_SHARE = 0.9

# Short or low-entropy replies (a session-control echo, an all-0xFF page) can legitimately be
# identical across ECUs, so the identical-bytes rule needs some substance to compare.
MIN_IDENTICAL_BYTES = 8
MIN_IDENTICAL_DISTINCT = 4

NEGATIVE_RESPONSE_SID = 0x7F


@dataclass(frozen=True)
class PayloadSample:
    """One stored reply. ``handle`` indexes the caller's own locator list, returned untouched."""

    handle: int
    rx: int
    pid: str
    payload: bytes


@dataclass(frozen=True)
class Misfile:
    """A sample that looks like ``likely_rx``'s reply rather than its own ECU's."""

    sample: PayloadSample
    likely_rx: int
    own_length: int | None
    likely_length: int | None
    likely_samples: int
    identical: bool

    @property
    def length_mismatch(self) -> bool:
        return self.own_length is not None and self.likely_length is not None


def _is_candidate(sample: PayloadSample) -> bool:
    return bool(sample.payload) and sample.payload[0] != NEGATIVE_RESPONSE_SID


def _signatures(samples: list[PayloadSample]) -> dict[tuple[int, str], tuple[int, int]]:
    """``(rx, pid) -> (established length, sample count)`` where one length dominates."""
    lengths: dict[tuple[int, str], Counter[int]] = defaultdict(Counter)
    for s in samples:
        lengths[(s.rx, s.pid)][len(s.payload)] += 1
    out: dict[tuple[int, str], tuple[int, int]] = {}
    for key, counts in lengths.items():
        total = sum(counts.values())
        length, hits = counts.most_common(1)[0]
        if total >= MIN_SIGNATURE_SAMPLES and hits / total >= MIN_SIGNATURE_SHARE:
            out[key] = (length, total)
    return out


def _substantial(payload: bytes) -> bool:
    return len(payload) >= MIN_IDENTICAL_BYTES and len(set(payload)) >= MIN_IDENTICAL_DISTINCT


def find_misfiled(samples: Iterable[PayloadSample]) -> list[Misfile]:
    """Return every sample that looks like another ECU's reply, in input order."""
    pool = [
        PayloadSample(s.handle, s.rx, s.pid.upper(), s.payload) for s in samples if _is_candidate(s)
    ]
    signatures = _signatures(pool)
    by_pid: dict[str, dict[int, tuple[int, int]]] = defaultdict(dict)
    for (rx, pid), sig in signatures.items():
        by_pid[pid][rx] = sig
    copies: dict[tuple[str, bytes], Counter[int]] = defaultdict(Counter)
    for s in pool:
        copies[(s.pid, s.payload)][s.rx] += 1

    findings: list[Misfile] = []
    for s in pool:
        own = signatures.get((s.rx, s.pid))
        by_length = {}
        if own is not None and len(s.payload) != own[0]:
            by_length = {
                rx: sig
                for rx, sig in by_pid[s.pid].items()
                if rx != s.rx and sig[0] == len(s.payload)
            }
        identical: dict[int, int] = {}
        if _substantial(s.payload):
            seen = copies[(s.pid, s.payload)]
            identical = {rx: n for rx, n in seen.items() if rx != s.rx and n >= seen[s.rx]}
        if not by_length and not identical:
            continue
        likely = _pick_likely(by_length, identical)
        sig = by_length.get(likely) or by_pid[s.pid].get(likely)
        findings.append(
            Misfile(
                sample=s,
                likely_rx=likely,
                own_length=own[0] if own and likely in by_length else None,
                likely_length=sig[0] if sig and likely in by_length else None,
                likely_samples=sig[1] if sig else identical.get(likely, 0),
                identical=likely in identical,
            )
        )
    return findings


def _pick_likely(by_length: dict[int, tuple[int, int]], identical: dict[int, int]) -> int:
    """Prefer an ECU both rules agree on, then the length rule, then the most copies."""
    both = [rx for rx in by_length if rx in identical]
    if both:
        return max(both, key=lambda rx: by_length[rx][1])
    if by_length:
        return max(by_length, key=lambda rx: by_length[rx][1])
    return max(identical, key=lambda rx: identical[rx])
