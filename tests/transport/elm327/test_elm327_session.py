"""The ELM327's own ECU wait during a wake burst (``transport/elm327/session.py``)."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from canair.transport.elm327 import session as elm327_session


def _terminal():
    sent: list[str] = []

    async def send_command(cmd, timeout=None):
        sent.append(cmd)
        return "OK"

    @contextlib.asynccontextmanager
    async def transaction():
        sent.append("<txn>")
        try:
            yield
        finally:
            sent.append("</txn>")

    return SimpleNamespace(
        send_command=send_command, transaction=transaction, elm_timeout_cmd="ATST96"
    ), sent


class TestShortEcuWait:
    @pytest.mark.asyncio
    async def test_the_wait_is_shortened_and_restored_in_one_transaction(self):
        term, sent = _terminal()
        async with elm327_session.short_ecu_wait(term):
            sent.append("burst")
        assert sent == ["<txn>", "ATST10", "burst", "ATST96", "</txn>"]

    @pytest.mark.asyncio
    async def test_it_is_restored_when_the_burst_fails(self):
        term, sent = _terminal()
        with pytest.raises(RuntimeError):
            async with elm327_session.short_ecu_wait(term):
                raise RuntimeError("boom")
        assert sent[-2:] == ["ATST96", "</txn>"]
