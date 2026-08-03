"""WiCAN WebSocket ELM327 terminal + device reboot.

:class:`WiCANTerminal` is the ELM327 engine (:class:`Elm327Terminal`) wired to a
:class:`~canlib.transport.channel.WebSocketChannel` — i.e. the WiCAN Pro's
``ws://host/ws`` ELM327 terminal, where the dongle performs ISO-TP. All the
ELM327 protocol logic lives in the transport-agnostic engine; only the WebSocket
channel and the HTTP :func:`reboot_wican` are WiCAN-specific here.
"""

import sys
from types import ModuleType
from typing import cast

from .transport.channel import WebSocketChannel
from .transport.elm327_terminal import Elm327Terminal


# The import is isolated in a helper so the module name isn't rebound to None
# (which would conflict with its module-typed import binding).
def _try_import_requests() -> ModuleType | None:
    try:
        import requests

        return requests
    except ImportError:
        return None


_requests_mod: ModuleType | None = _try_import_requests()
HAS_REQUESTS = _requests_mod is not None


class WiCANTerminal(Elm327Terminal):
    """ELM327 engine over a WiCAN WebSocket terminal (``ws://host/ws``)."""

    def __init__(
        self,
        host: str,
        timeout: float = 3.0,
        verbose: bool = False,
        unsafe: bool = False,
        hk_f1xx_offset: bool = False,
    ):
        self.host = host
        channel = WebSocketChannel(host, verbose=verbose)
        super().__init__(
            channel,
            timeout=timeout,
            verbose=verbose,
            unsafe=unsafe,
            hk_f1xx_offset=hk_f1xx_offset,
        )

    # The raw WebSocket is exposed for the WiCAN-only paths that read it directly
    # (the SKM relay-wake ritual) and for tests that inject a fake WebSocket.
    @property
    def ws(self):
        return cast(WebSocketChannel, self._channel).ws

    @ws.setter
    def ws(self, value) -> None:
        cast(WebSocketChannel, self._channel).ws = value

    @property
    def url(self) -> str:
        return cast(WebSocketChannel, self._channel).url


def reboot_wican(host: str):
    """Reboot WiCAN device via HTTP POST to restore AutoPID mode."""
    if not HAS_REQUESTS:
        print(
            "  Cannot reboot: 'requests' module not installed. Run: pip3 install requests",
            file=sys.stderr,
        )
        print(
            f"  Manual reboot: curl -X POST http://{host}/system_reboot -d reboot",
            file=sys.stderr,
        )
        return False

    assert _requests_mod is not None  # guaranteed by HAS_REQUESTS check above
    url = f"http://{host}/system_reboot"
    try:
        resp = _requests_mod.post(url, data="reboot", timeout=5)
        print(f"Rebooting WiCAN... ({resp.status_code})")
        return True
    except _requests_mod.RequestException as e:
        print(f"  FAILED to reboot: {e}", file=sys.stderr)
        return False
