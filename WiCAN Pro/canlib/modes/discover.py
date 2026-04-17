"""Discover ECUs by sweeping a range of TX addresses."""

import asyncio
import json
import sys

from ..terminal import WiCANTerminal


async def mode_discover(
    terminal: WiCANTerminal,
    addr_range: tuple[int, int],
    verbose: bool,
    as_json: bool,
    delay: float = 0.2,
):
    """Sweep a range of CAN arbitration IDs to find responding ECUs.

    For each address, sets the ELM327 header and sends a default session
    request (10 01). ECUs that respond positively or with a known NRC
    are considered "alive".

    Args:
        addr_range: (start, end) TX IDs inclusive (e.g. 0x700, 0x7EF).
        delay: Seconds to wait between addresses to avoid overwhelming
            the WiCAN WebSocket. Default 0.2s.
    """
    start, end = addr_range
    total = end - start + 1

    print(
        f"\n  ECU discovery: TX 0x{start:03X}..0x{end:03X} ({total} addresses)"
        f"\n  Probe: 10 01 (default session request)"
        f"\n  Delay: {delay}s between addresses"
    )
    print()

    alive = []
    errors = []

    for i, tx_id in enumerate(range(start, end + 1)):
        try:
            await terminal.set_header(tx_id)
            response = await terminal.send_uds("1001", timeout=2.0)
        except Exception as e:
            errors.append((tx_id, str(e)))
            if verbose:
                print(f"  ! 0x{tx_id:03X}: {e}")
            # On connection errors, wait longer before retrying
            await asyncio.sleep(1.0)
            continue

        if response["ok"]:
            n_bytes = len(response["bytes"])
            alive.append((tx_id, "positive", response["hex"]))
            print(f"  + 0x{tx_id:03X}: OK ({n_bytes} bytes) {response['hex']}")
        elif response.get("nrc") is not None:
            nrc = response["nrc"]
            desc = response["nrc_desc"]
            # NRC means the ECU is alive but rejected the request
            alive.append((tx_id, f"NRC 0x{nrc:02X}", desc))
            print(f"  ~ 0x{tx_id:03X}: NRC 0x{nrc:02X} ({desc}) — ECU alive")
        else:
            # NO DATA / no response — not an ECU at this address
            if verbose:
                error = response.get("error", "no response")
                print(f"  - 0x{tx_id:03X}: {error}")

        # Progress indicator (non-verbose)
        if not verbose and not response["ok"] and response.get("nrc") is None:
            done = i + 1
            if done % 16 == 0 or done == total:
                pct = done / total * 100
                print(
                    f"  ... {done}/{total} ({pct:.0f}%)",
                    end="\r",
                    file=sys.stderr,
                )

        # Pace requests to avoid overwhelming the WiCAN
        await asyncio.sleep(delay)

    print("\n  --- Discovery Results ------------------------------------")
    print(f"  Alive:  {len(alive)}")
    print(f"  Silent: {total - len(alive) - len(errors)}")
    print(f"  Errors: {len(errors)}")

    if alive:
        print(f"\n  Responding addresses:")
        for tx_id, status, detail in alive:
            rx_id = tx_id + 8
            print(f"    0x{tx_id:03X} (RX 0x{rx_id:03X}) — {status}: {detail}")

    if errors:
        print(f"\n  Errors:")
        for tx_id, err in errors:
            print(f"    0x{tx_id:03X}: {err}")

    if as_json:
        out = {
            "range": f"0x{start:03X}-0x{end:03X}",
            "alive": [
                {"tx": f"0x{t:03X}", "rx": f"0x{t + 8:03X}", "status": s, "detail": d}
                for t, s, d in alive
            ],
            "errors": [{"tx": f"0x{t:03X}", "error": e} for t, e in errors],
        }
        print(json.dumps(out, indent=2))

    print()
