"""Tests for canair.render.formatting — how sizes, rates and durations are written."""

from __future__ import annotations

import pytest

from canair.render.formatting import duration_text, rate_text, size_text


@pytest.mark.parametrize(
    ("n", "text"),
    [(0, "0 B"), (1023, "1023 B"), (1024, "1.0 KiB"), (40192, "39.2 KiB"), (1572864, "1.5 MiB")],
)
def test_sizes(n, text):
    assert size_text(n) == text


def test_rates_are_whole_bytes_per_second():
    assert rate_text(187.6) == "188 B/s"


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0.4, "0 s"), (25, "25 s"), (45, "1 min"), (3120, "52 min"), (4380, "1 h 13 min")],
)
def test_durations(seconds, text):
    assert duration_text(seconds) == text
