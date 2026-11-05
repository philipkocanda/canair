"""TesterPresent keepalive mode."""

import asyncio
from datetime import datetime

from canair.render import messages
from canair.transport.elm327.addressing import FUNCTIONAL_11BIT_ID
from canair.transport.elm327.terminal import Elm327Terminal
from canair.uds.nrc import is_negative_response
from canair.uds.parse import reply_lines
from canair.uds.services import TESTER_PRESENT, TESTER_PRESENT_REQUEST, positive_response_sid

from ..progress_line import ProgressLine

_POSITIVE = bytes([positive_response_sid(TESTER_PRESENT)])


def parse_target(target: str | None) -> int | None:
    """The request id a ``!tester`` target names (``7E0``/``0x7E0``), or None if invalid.

    No target means the OBD-II functional broadcast id.
    """
    if not target:
        return FUNCTIONAL_11BIT_ID
    text = target.strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    try:
        tx_id = int(text, 16)
    except ValueError:
        return None
    return tx_id if 0 <= tx_id <= 0x1FFFFFFF else None


async def mode_tester_present(
    terminal: Elm327Terminal, target: str | None, interval: float, verbose: bool
):
    """Send TesterPresent (3E00) at regular intervals to keep a session alive.

    Runs until interrupted with Ctrl+C.
    """
    tx_id = parse_target(target)
    if tx_id is None:
        messages.error(f"invalid target {target!r}: give the ECU's request id in hex, e.g. 7E0")
        return
    if target:
        print(f"\n  TesterPresent -- targeting 0x{tx_id:03X} every {interval:.1f}s")
    else:
        print(f"\n  TesterPresent -- broadcast (0x7DF) every {interval:.1f}s")

    print("  Press Ctrl+C to stop.\n")

    count = 0
    status = ProgressLine()

    def show(line: str) -> None:
        """The counter rewrites one line on a terminal; elsewhere it is a line per message."""
        if status.enabled:
            status.update(line)
        else:
            print(line)

    try:
        while True:
            count += 1
            # Through set_header, so a receive filter pinned for an earlier ECU cannot
            # hide the replies (a broadcast restores the adapter's own filter for every
            # responder), and in one transaction with the request so nothing re-points
            # the header in between. Cached, so only the first round sends anything.
            async with terminal.transaction():
                await terminal.set_header(tx_id)
                resp = await terminal.send_command(TESTER_PRESENT_REQUEST, timeout=2.0)

            clean = resp.replace(" ", "").replace("\n", " ").strip()
            ts = datetime.now().strftime("%H:%M:%S")

            if verbose:
                print(f"  [{ts}] #{count} 3E00 -> {clean}")
            else:
                replies = reply_lines(resp)
                n_pos = sum(r[:1] == _POSITIVE for r in replies)
                n_neg = sum(is_negative_response(r, TESTER_PRESENT) for r in replies)
                has_nodata = "NODATA" in clean.upper().replace(" ", "")
                if has_nodata:
                    show(f"  [{ts}] #{count} NO DATA")
                else:
                    parts = []
                    if n_pos:
                        parts.append(f"{n_pos} OK")
                    if n_neg:
                        parts.append(f"{n_neg} NRC")
                    show(f"  [{ts}] #{count} {', '.join(parts) if parts else clean[:40]}")

            await asyncio.sleep(interval)

    except (KeyboardInterrupt, asyncio.CancelledError):
        status.clear()
        print(f"\n  Stopped after {count} messages.")
