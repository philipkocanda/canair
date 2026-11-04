"""A programmable fake WebSocket for ELM327 terminal tests."""

import asyncio

from canlib.transport.wican_terminal import WiCANTerminal


class ProgrammableWS:
    """FakeWS returning a queued reply per send (defaults to NO DATA)."""

    def __init__(self, replies):
        self.sent: list[str] = []
        self._replies = list(replies)
        self._q: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, data: str):
        self.sent.append(data)
        reply = self._replies.pop(0) if self._replies else "NO DATA\r>"
        await self._q.put(reply)

    async def recv(self) -> str:
        return await self._q.get()

    async def close(self):
        pass


def term_prog(replies) -> WiCANTerminal:
    t = WiCANTerminal(host="test")
    t.ws = ProgrammableWS(replies)
    return t
