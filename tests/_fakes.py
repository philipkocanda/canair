"""Shared async fake terminal for device-free mode/command tests.

canair talks to the bus through a terminal exposing a small async surface
(``set_header``/``send_uds``/``send_command``/``enter_extended_session``/
``close``); both real transports (``WiCANTerminal``, ``RawTerminal``) implement
it identically, and every live command is driven through the shared
``dispatch_mode`` against *that surface only*. Tests therefore drive modes with a
fake exposing the same surface — historically hand-rolled once per test file
(``MockTerminal``/``FakeTerminal``/``FlakyTerminal``/ad-hoc ``AsyncMock``), with
six divergent ``send_uds`` signatures and five recorder conventions.

``FakeTerminal`` consolidates that plumbing: the faithful async surface (with the
``enter_extended_session(wake, mode)`` keyword the dual-transport contract needs),
a scriptable per-request response map, and a uniform set of recorders. Per-test
*scripting* stays local — pass a ``responses`` map, tweak ``default``/
``flaky_recover``/``session_result``, or subclass and override ``send_uds``.

Response dicts mirror :func:`canlib.uds_parse.parse_uds_response`: positives are
``{"ok": True, "hex", "bytes", "raw"}`` (build with :func:`ok`); negatives are
``{"ok": False, "nrc", "nrc_desc", ...}`` or ``{"ok": False, "error"}``.
"""

from __future__ import annotations

NO_DATA: dict = {"ok": False, "error": "NO DATA", "raw": "NO DATA"}


def ok(hex_str: str) -> dict:
    """Build a positive UDS response dict from a hex payload string."""
    clean = hex_str.replace(" ", "")
    return {"ok": True, "bytes": bytes.fromhex(clean), "hex": clean.upper(), "raw": hex_str}


def nrc(code: int, desc: str = "") -> dict:
    """Build a negative-response (NRC) dict."""
    d: dict = {"ok": False, "nrc": code}
    if desc:
        d["nrc_desc"] = desc
    return d


class FakeTerminal:
    """Scriptable async fake exposing the real terminal surface.

    Args:
        responses: request-string → response-dict map. Keyed by request by
            default, or by ``(header_tx_id, request)`` when ``key_by_header``.
        default: response returned for an unscripted request (default: NO DATA).
        flaky_recover: request → hex map modelling a slow/asleep ECU — the first
            time a request is seen it returns ``default``, subsequent times the
            positive ``ok(hex)`` (the old ``FlakyTerminal`` behaviour).
        send_command_reply: what ``send_command`` returns (default ``"OK"``).
        session_result: what ``enter_extended_session`` returns (default
            ``(True, None)``).
        key_by_header: look responses up by ``(current_header, request)``.

    Recorders (assert against these): ``sent`` (request strings), ``headers``
    (tx_ids), ``sessions`` (``(wake, mode)`` tuples), ``calls`` (``(method, arg)``
    tuples), ``uds_kwargs`` (per-call ``send_uds`` keyword dicts).
    """

    def __init__(
        self,
        responses: dict | None = None,
        *,
        default: dict | None = None,
        flaky_recover: dict | None = None,
        send_command_reply: str = "OK",
        session_result: tuple = (True, None),
        key_by_header: bool = False,
    ) -> None:
        self.responses = responses or {}
        self.default = NO_DATA if default is None else default
        self.flaky_recover = flaky_recover or {}
        self.send_command_reply = send_command_reply
        self.session_result = session_result
        self.key_by_header = key_by_header

        self.header: int | None = None
        self.sent: list[str] = []
        self.headers: list[int] = []
        self.sessions: list[tuple] = []
        self.calls: list[tuple] = []
        self.uds_kwargs: list[dict] = []
        self._seen: set[str] = set()

    async def set_header(self, tx_id: int) -> None:
        self.header = tx_id
        self.headers.append(tx_id)
        self.calls.append(("set_header", tx_id))

    async def send_uds(
        self,
        req: str,
        timeout: float | None = None,
        expected_sid: int | None = None,
        expected_did: int | None = None,
        expected_echo: bytes | None = None,
        retries: int = 0,
        **kw,
    ) -> dict:
        self.sent.append(req)
        self.calls.append(("send_uds", req))
        self.uds_kwargs.append(
            {
                "timeout": timeout,
                "expected_sid": expected_sid,
                "expected_did": expected_did,
                "expected_echo": expected_echo,
                "retries": retries,
                **kw,
            }
        )
        if req in self.flaky_recover:
            if req not in self._seen:
                self._seen.add(req)
                return dict(self.default)
            return ok(self.flaky_recover[req])
        key = (self.header, req) if self.key_by_header else req
        return dict(self.responses.get(key, self.default))

    async def send_command(self, cmd: str, timeout: float | None = None) -> str:
        self.calls.append(("send_command", cmd))
        return self.send_command_reply

    async def enter_extended_session(self, wake: bool = False, mode: str = "03") -> tuple:
        self.sessions.append((wake, mode))
        self.calls.append(("enter_extended_session", (wake, mode)))
        return self.session_result

    async def close(self) -> None:
        self.calls.append(("close", None))
