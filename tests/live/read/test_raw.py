"""Tests for canair.live.read.raw — a malformed request never reaches the terminal."""

import pytest

from canair.live.read.raw import mode_raw
from tests.support.fakes import FakeTerminal


@pytest.mark.asyncio
async def test_odd_length_request_sends_nothing():
    term = FakeTerminal()
    with pytest.raises(SystemExit) as exc:
        await mode_raw(term, "7B3:2313000000040", False, False)
    assert exc.value.code == 2
    assert term.sent == []
