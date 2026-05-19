"""TesterPresent keepalive mode."""

import asyncio
from datetime import datetime

from ..terminal import WiCANTerminal


async def mode_tester_present(
    terminal: WiCANTerminal, target: str | None, interval: float, verbose: bool
):
    """Send TesterPresent (3E00) at regular intervals to keep a session alive.

    Runs until interrupted with Ctrl+C.
    """
    if target:
        tx_id = target.upper()
        print(f"\n  TesterPresent -- targeting 0x{tx_id} every {interval:.1f}s")
        await terminal.send_command(f"ATSH{tx_id}")
        await terminal.send_command(f"ATFCSH{tx_id}")
    else:
        tx_id = "7DF"
        print(f"\n  TesterPresent -- broadcast (0x7DF) every {interval:.1f}s")
        await terminal.send_command("ATSH7DF")
        await terminal.send_command("ATFCSH7DF")

    print("  Press Ctrl+C to stop.\n")

    count = 0
    try:
        while True:
            count += 1
            resp = await terminal.send_command("3E00", timeout=2.0)

            clean = resp.replace(" ", "").replace("\n", " ").strip()
            ts = datetime.now().strftime("%H:%M:%S")

            if verbose:
                print(f"  [{ts}] #{count} 3E00 -> {clean}")
            else:
                n_pos = clean.count("7E00")
                n_neg = clean.upper().count("7F3E")
                has_nodata = "NODATA" in clean.upper().replace(" ", "")
                if has_nodata:
                    print(f"  [{ts}] #{count} NO DATA", end="\r")
                else:
                    parts = []
                    if n_pos:
                        parts.append(f"{n_pos} OK")
                    if n_neg:
                        parts.append(f"{n_neg} NRC")
                    print(
                        f"  [{ts}] #{count} {', '.join(parts) if parts else clean[:40]}", end="\r"
                    )

            await asyncio.sleep(interval)

    except (KeyboardInterrupt, asyncio.CancelledError):
        print(f"\n\n  Stopped after {count} messages.")
