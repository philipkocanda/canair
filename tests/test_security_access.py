"""Tests for SecurityAccess (0x27) — pair-solver, seed-length handling, blocklist."""

from __future__ import annotations

import pytest

from canlib.elm327 import check_command_safety
from canlib.modes.multi import SECURITY_ALGORITHMS, _exec_security, solve_key_pair


# ── solve_key_pair (offline algorithm identification) ────────────────────────


def test_solve_key_pair_finds_known_algorithm():
    seed = 0x12345678
    key = seed ^ 0x0D0B0507  # the "xor-0d0b0507" algorithm
    names = [n for n, _ in solve_key_pair(seed, key, seed_len=4)]
    assert "xor-0d0b0507" in names


def test_solve_key_pair_echo_and_not():
    seed = 0xDEADBEEF
    assert "same" in [n for n, _ in solve_key_pair(seed, seed, seed_len=4)]
    assert "not" in [n for n, _ in solve_key_pair(seed, (~seed) & 0xFFFFFFFF, seed_len=4)]


def test_solve_key_pair_no_match():
    # A key no simple transform produces (0 seed → 0 for most, so use a nonzero seed
    # and a key unlikely to be reproduced).
    assert solve_key_pair(0x00000001, 0x87654321, seed_len=4) == []


def test_solve_key_pair_masks_to_seed_width():
    # 2-byte seed: only the low 16 bits matter.
    seed = 0xABCD
    key = (seed ^ 0x0D0B0507) & 0xFFFF
    names = [n for n, _ in solve_key_pair(seed, key, seed_len=2)]
    assert "xor-0d0b0507" in names


# ── check_command_safety: KWP session-mode hardening ─────────────────────────


def test_programming_sessions_blocked():
    assert check_command_safety("1002") is not None  # UDS programmingSession
    assert check_command_safety("1085") is not None  # KWP2000 ECU programming mode


def test_unknown_kwp_session_modes_blocked():
    # 0x8x band that isn't the known-safe 0x81 → blocked (may be prog/dev mode).
    assert check_command_safety("1090") is not None
    assert check_command_safety("1086") is not None
    assert check_command_safety("10FF") is not None


def test_safe_sessions_allowed():
    assert check_command_safety("1001") is None  # default
    assert check_command_safety("1003") is None  # UDS extended
    assert check_command_safety("1081") is None  # KWP standard


# ── _exec_security: variable seed length + key width ─────────────────────────


class _FakeTerminal:
    """Captures 2702 key requests; returns a fixed seed for 2701."""

    def __init__(self, seed_bytes: bytes):
        self._seed_bytes = seed_bytes
        self.key_requests: list[str] = []

    async def set_header(self, tx_id):
        pass

    async def send_uds(self, req, timeout=5.0, **kw):
        if req == "2701":
            return {"ok": True, "bytes": bytes([0x67, 0x01]) + self._seed_bytes,
                    "hex": (bytes([0x67, 0x01]) + self._seed_bytes).hex().upper()}
        if req.startswith("2702"):
            self.key_requests.append(req)
            return {"ok": True, "bytes": bytes([0x67, 0x02])}  # accepted
        return {"ok": False, "error": "unexpected"}


class _FakeSM:
    def __init__(self, terminal):
        self.terminal = terminal

    async def keepalive_stale(self, *a, **k):
        pass

    async def open_session(self, *a, **k):
        return True


@pytest.mark.asyncio
async def test_exec_security_4byte_seed_key_width():
    term = _FakeTerminal(bytes([0x11, 0x22, 0x33, 0x44]))
    ok = await _exec_security(_FakeSM(term), "7A0", ["same"], {}, verbose=False)
    assert ok is True
    # "same" echoes the seed; key must be the 4-byte seed, 8 hex digits.
    assert term.key_requests == ["270211223344"]


@pytest.mark.asyncio
async def test_exec_security_2byte_seed_key_width():
    term = _FakeTerminal(bytes([0xAB, 0xCD]))
    ok = await _exec_security(_FakeSM(term), "7A0", ["same"], {}, verbose=False)
    assert ok is True
    # Key formatted to the 2-byte seed width (4 hex digits), not padded to 8.
    assert term.key_requests == ["2702ABCD"]


@pytest.mark.asyncio
async def test_exec_security_all_zero_seed_reports_unlocked():
    term = _FakeTerminal(bytes([0x00, 0x00, 0x00, 0x00]))
    ok = await _exec_security(_FakeSM(term), "7A0", ["same"], {}, verbose=False)
    # All-zero seed → treated as already unlocked; no key sent.
    assert ok is True
    assert term.key_requests == []


def test_all_algorithms_are_callable():
    # Guard: every registered algorithm runs without error on a sample seed.
    for name, (desc, fn) in SECURITY_ALGORITHMS.items():
        assert isinstance(desc, str)
        fn(0x12345678)  # must not raise
