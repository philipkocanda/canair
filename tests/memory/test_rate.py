"""Tests for canair.memory.rate — the image's rate controller (pure)."""

from __future__ import annotations

from canair.memory.rate import (
    ERROR_WINDOW,
    RECOVER_AFTER,
    THROTTLE_STEP_MS,
    advance,
    ceiling,
    digit_block,
    preferred_block,
    start_rate,
)


def _run(rate, events):
    for event in events:
        rate = advance(rate, event)
    return rate


def test_the_ceiling_is_the_smaller_of_max_block_and_the_limit():
    assert ceiling(64, None, 255) == 64
    assert ceiling(64, 32, 255) == 32
    assert ceiling(None, None, 255) == 255


def test_the_digit_block_is_61_bytes_on_classic_can():
    """A 62-byte reply (63 + 61 bytes) is 9 frames; one more byte would need 10."""
    assert digit_block(64) == 61
    assert digit_block(32) == 32
    assert preferred_block(64, digit=True) == 61
    assert preferred_block(64, digit=False) == 64


def test_one_error_does_not_back_off():
    rate = _run(start_rate(61), ["ok", "halving", "ok"])
    assert (rate.block, rate.throttle_ms) == (61, 0)


def test_a_retry_is_neither_an_error_nor_a_clean_read():
    rate = _run(start_rate(61), ["retry"] * 10)
    assert (rate.block, rate.throttle_ms, rate.errors, rate.clean) == (61, 0, (), 0)
    assert rate.count == 10


def test_two_errors_in_the_window_halve_and_throttle():
    rate = _run(start_rate(61), ["reread", *["ok"] * (ERROR_WINDOW - 2), "drop"])
    assert (rate.block, rate.throttle_ms) == (30, THROTTLE_STEP_MS)


def test_errors_further_apart_do_not_add_up():
    rate = _run(start_rate(61), ["reread", *["ok"] * ERROR_WINDOW, "drop"])
    assert rate.block == 61


def test_the_block_never_falls_below_the_floor():
    rate = _run(start_rate(61), ["drop", "drop"] * 10)
    assert rate.block == 16


def test_clean_reads_recover_one_step_at_a_time():
    rate = _run(start_rate(61), ["drop", "drop", "drop", "drop"])
    assert rate.block == 16
    rate = _run(rate, ["ok"] * RECOVER_AFTER)
    assert (rate.block, rate.throttle_ms) == (32, THROTTLE_STEP_MS)
    rate = _run(rate, ["ok"] * RECOVER_AFTER)
    assert (rate.block, rate.throttle_ms) == (61, 0)
    assert _run(rate, ["ok"] * RECOVER_AFTER).block == 61  # never above the preferred


def test_a_resume_starts_at_the_block_reached_never_above():
    assert start_rate(61, block=30).block == 30
    assert start_rate(61, block=200).block == 61
