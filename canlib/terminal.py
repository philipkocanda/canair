"""WiCAN WebSocket ELM327 terminal + device reboot.

:class:`WiCANTerminal` is the ELM327 engine (:class:`Elm327Terminal`) wired to a
WebSocket channel — the WiCAN Pro's ELM327 terminal, where the dongle performs
ISO-TP: ``ws://host/ws`` on firmware 4.x
(:class:`~canlib.transport.channel.WebSocketChannel`) or ``ws://host/ws/obd`` on
firmware v6 (:class:`~canlib.transport.channel.ObdWebSocketChannel`). All the
ELM327 protocol logic lives in the transport-agnostic engine; only the WebSocket
channel and the HTTP :func:`reboot_wican` are WiCAN-specific here.
"""

import sys
from typing import cast

import requests

from .transport.channel import ObdWebSocketChannel, WebSocketChannel
from .transport.elm327_frame_count import CountKey
from .transport.elm327_terminal import Elm327Terminal


class WiCANTerminal(Elm327Terminal):
    """ELM327 engine over a WiCAN WebSocket terminal.

    ``obd_channel`` selects firmware v6's raw ``/ws/obd`` channel instead of
    4.x's ``/ws`` terminal; the caller decides from the probed firmware.
    """

    def __init__(
        self,
        host: str,
        timeout: float = 3.0,
        verbose: bool = False,
        unsafe: bool = False,
        hk_f1xx_offset: bool = False,
        expected_responses: bool = True,
        response_frames: dict[CountKey, int] | None = None,
        obd_channel: bool = False,
    ):
        channel_type = ObdWebSocketChannel if obd_channel else WebSocketChannel
        channel = channel_type(host, verbose=verbose)
        super().__init__(
            channel,
            timeout=timeout,
            verbose=verbose,
            unsafe=unsafe,
            hk_f1xx_offset=hk_f1xx_offset,
            expected_responses=expected_responses,
            response_frames=response_frames,
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


def reboot_wican(host: str) -> bool:
    """Reboot a WiCAN 4.x device via HTTP POST to restore AutoPID mode.

    Returns True only when the device accepted the request. Skipped with a note
    on firmware v6, which has no ``/system_reboot`` and resumes AutoPID itself.
    """
    from .wican_firmware import check, print_verdict

    verdict = check(host, "reboot")
    if verdict.level == "skip":
        print_verdict(verdict)
        return False

    url = f"http://{host}/system_reboot"
    try:
        resp = requests.post(url, data="reboot", timeout=5)
    except requests.RequestException as e:
        print(f"  FAILED to reboot: {e}", file=sys.stderr)
        return False
    if not resp.ok:
        print(f"  FAILED to reboot: HTTP {resp.status_code} from {url}", file=sys.stderr)
        return False
    print(f"Rebooting WiCAN... ({resp.status_code})")
    return True
