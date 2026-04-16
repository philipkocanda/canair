"""Wake sleeping ECUs via SKM relay control."""

import asyncio
import json
import re
import time

from ..terminal import WiCANTerminal

# SKM relay control DIDs
SKM_RELAYS = {
    "acc":  ("B108", "ACC (Accessory)"),
    "ign1": ("B109", "IGN1 (Ignition 1)"),
    "ign2": ("B10A", "IGN2 (Ignition 2)"),
    "start": ("B10B", "Start Relay"),
}

# Magic bytes required for SKM IOControl ON
SKM_MAGIC = "0A0A05"


async def mode_skm_wakeup(terminal: WiCANTerminal, level: str, verbose: bool):
    """Wake sleeping ECUs via SKM relay control.

    Sends a broadcast 3E00 + 1001 to nudge the SKM awake, then establishes
    an extended diagnostic session and activates the requested relay level.
    """
    if level not in SKM_RELAYS:
        print(f"  Unknown level: {level}. Available: {', '.join(SKM_RELAYS.keys())}")
        return False

    did, desc = SKM_RELAYS[level]

    if level == "start":
        print("  !! WARNING: Start Relay can crank the motor!")
        print("  !! Only proceed if the car is in Park and safe conditions.")
        try:
            confirm = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("  !! Type 'YES' to proceed: "))
        except (EOFError, KeyboardInterrupt):
            confirm = ""
        if confirm.strip() != "YES":
            print("  Aborted.")
            return False

    print(f"\n  SKM Wakeup -- {desc}")
    print(f"  ---------------------------------")

    # Step 1: Broadcast to wake SKM
    print(f"  [1/3] Broadcasting wake signal (3E00 + 1001 on 0x7DF)...")
    await terminal.send_command("ATSH7DF")
    await terminal.send_command("ATFCSH7DF")

    for i in range(3):
        resp = await terminal.send_command("3E00")
        if verbose:
            print(f"        3E00 [{i+1}] -> {resp}")

    resp = await terminal.send_command("1001")
    if verbose:
        print(f"        1001 -> {resp}")

    await asyncio.sleep(0.5)

    # Step 2: Extended diagnostic session on SKM
    print(f"  [2/3] Establishing extended session on SKM (0x7A5)...")
    await terminal.send_command("ATSH7A5")
    await terminal.send_command("ATFCSH7A5")

    session_ok = False
    for attempt in range(8):
        resp = await terminal.send_command("1003", timeout=3.0)
        if "50 03" in resp or "5003" in resp:
            session_ok = True
            if verbose:
                print(f"        1003 -> {resp} (attempt {attempt + 1})")
            break
        if verbose:
            print(f"        1003 -> {resp} (attempt {attempt + 1})")
        await asyncio.sleep(1.0)

    if not session_ok:
        print(f"  FAILED: SKM did not respond to extended session request.")
        print(f"  The SKM may be asleep. It only responds when the CAN bus is active")
        print(f"  (e.g. during charging). See skm-wakeup.md for details.")
        return False

    print(f"        Session established.")

    # Step 3: Send relay ON command
    await terminal._drain()
    cmd = f"2F{did}03{SKM_MAGIC}"
    print(f"  [3/3] Sending {desc} ON ({cmd})...")
    resp = await terminal.send_command(cmd, timeout=10.0)

    clean = resp.replace(" ", "").replace("\n", "").upper()
    is_fc_only = clean in ("F00", "FC00", "F0", "FC0") or \
                 (len(clean) <= 4 and clean.startswith("F"))

    if is_fc_only or ("7F2F78" in clean and "6F" not in clean):
        if verbose:
            reason = "FC echo" if is_fc_only else "pending NRC"
            print(f"        Initial response: {resp.strip()} ({reason})")
            print(f"        Waiting for UDS response...")
        deadline = time.monotonic() + 10.0
        extra_parts = []
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(
                    terminal.ws.recv(), timeout=min(remaining, 1.0))
                if isinstance(msg, str):
                    try:
                        parsed = json.loads(msg)
                        if parsed.get("type") == "term_out":
                            data = parsed["data"]
                            extra_parts.append(data)
                            if verbose:
                                print(f"        Recv: {data.strip()!r}")
                        continue
                    except json.JSONDecodeError:
                        pass
                    extra_parts.append(msg)
                    if verbose:
                        print(f"        Recv: {msg.strip()!r}")
                combined = "".join(extra_parts).replace(" ", "").upper()
                if "6FB1" in combined or "6F" in combined:
                    break
                if re.search(r"7F2F(?!78)[0-9A-Fa-f]{2}", combined):
                    break
            except asyncio.TimeoutError:
                combined = "".join(extra_parts).replace(" ", "").upper()
                if "7F2F78" in combined and "6F" not in combined:
                    continue
                break
            except Exception:
                break
        if extra_parts:
            resp = resp + "\n" + "".join(extra_parts)
            if verbose:
                print(f"        Full response: {resp.strip()}")

    success = f"6F{did[0:2]}" in resp.replace(" ", "").upper() or \
              f"6FB1" in resp.replace(" ", "").upper()

    if success:
        print(f"        {desc} activated!")
        print(f"\n  Woke ECUs should now respond to queries.")
    else:
        clean = resp.replace(" ", "").upper()
        if "7F2F7F" in clean:
            print(f"        FAILED: serviceNotSupportedInActiveSession")
            print(f"        The extended session may have expired. Try again.")
        elif "7F2F78" in clean and "6F" not in clean:
            print(f"        Pending response received but no positive confirmation.")
        else:
            print(f"        Response: {resp}")
            print(f"        Could not confirm relay activation.")

    return success
