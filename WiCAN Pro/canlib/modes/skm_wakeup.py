"""Wake sleeping ECUs via SKM relay control."""

import asyncio
import json
import re
import time

from ..terminal import WiCANTerminal

# SKM relay control DIDs
SKM_RELAYS = {
    "acc": ("B108", "ACC (Accessory)"),
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
                None, lambda: input("  !! Type 'YES' to proceed: ")
            )
        except (EOFError, KeyboardInterrupt):
            confirm = ""
        if confirm.strip() != "YES":
            print("  Aborted.")
            return False

    print(f"\n  SKM Wakeup -- {desc}")
    print(f"  ---------------------------------")

    # Step 1: Rapid-fire 1001 to wake SKM transceiver from deep sleep
    # SKM wakes on the 2nd frame but has a ~2s sleep timer — needs fast CAN
    # traffic (64ms timeout) to stay awake. Direct to 0x7A5, no broadcast needed.
    print(f"  [1/4] Waking SKM (rapid-fire 1001 at 64ms timeout)...")
    await terminal.send_command("ATSH7A5")
    await terminal.send_command("ATFCSH7A5")

    # Save current timeout and switch to fast mode
    await terminal.send_command("ATST10")  # 64ms timeout

    skm_awake = False
    for attempt in range(10):
        resp = await terminal.send_command("1001", timeout=3.0)
        if "50 01" in resp or "5001" in resp:
            skm_awake = True
            if verbose:
                print(f"        1001 -> {resp} (attempt {attempt + 1})")
            break
        if verbose:
            print(f"        1001 -> {resp} (attempt {attempt + 1})")

    if not skm_awake:
        # Restore timeout before returning
        await terminal.send_command(terminal.elm_timeout_cmd)
        print(f"  FAILED: SKM did not wake after 10 rapid-fire attempts.")
        print(f"  The SKM transceiver may be fully unpowered.")
        return False

    print(f"        SKM awake (attempt {attempt + 1}).")

    # Step 2: Extended diagnostic session — send immediately while SKM is awake
    print(f"  [2/4] Establishing extended session on SKM (0x7A5)...")

    session_ok = False
    for attempt in range(5):
        resp = await terminal.send_command("1003", timeout=3.0)
        if "50 03" in resp or "5003" in resp:
            session_ok = True
            break
        if verbose:
            print(f"        1003 -> {resp} (attempt {attempt + 1})")

    # Restore user's timeout
    await terminal.send_command(terminal.elm_timeout_cmd)

    if not session_ok:
        print(f"  FAILED: SKM awake but extended session failed.")
        return False

    print(f"        Extended session (10 03) established.")

    # Step 3: Send relay ON command
    await terminal._drain()
    cmd = f"2F{did}03{SKM_MAGIC}"
    print(f"  [3/4] Sending {desc} ON ({cmd})...")
    resp = await terminal.send_command(cmd, timeout=10.0)

    clean = resp.replace(" ", "").replace("\n", "").upper()
    is_fc_only = clean in ("F00", "FC00", "F0", "FC0") or (
        len(clean) <= 4 and clean.startswith("F")
    )

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
                    terminal.ws.recv(), timeout=min(remaining, 1.0)
                )
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

    success = (
        f"6F{did[0:2]}" in resp.replace(" ", "").upper()
        or f"6FB1" in resp.replace(" ", "").upper()
    )

    if not success:
        clean = resp.replace(" ", "").upper()
        if "7F2F7F" in clean:
            print(f"        FAILED: serviceNotSupportedInActiveSession")
            print(f"        The extended session may have expired. Try again.")
        elif "7F2F78" in clean and "6F" not in clean:
            print(f"        Pending response received but no positive confirmation.")
        else:
            print(f"        Response: {resp}")
            print(f"        Could not confirm relay activation.")
        return False

    print(f"        UDS positive response received.")

    # Step 4: Verify ACC actually engaged by reading IGPM ignition state
    # SKM returns positive UDS response even without keyfob nearby,
    # but the relay physically doesn't close. Read IGPM BC03 to verify.
    print(f"  [4/4] Verifying relay engagement via IGPM (0x770)...")
    await terminal.send_command("ATSH770")
    await terminal.send_command("ATFCSH770")

    # IGPM may be in deep sleep — wake it first with 1001
    wake_resp = await terminal.send_command("1001", timeout=3.0)
    # 1001 may return NO DATA when IGPM is in deep sleep — that's expected

    # IGPM needs extended session for 22BCxx reads
    igpm_resp = await terminal.send_command("1003", timeout=3.0)
    if "50 03" not in igpm_resp and "5003" not in igpm_resp:
        print(f"        WARNING: Could not enter IGPM extended session.")
        print(f"        Cannot verify relay state — proceed with caution.")
        return True

    # Small delay for relay state to settle
    await asyncio.sleep(0.3)

    # Read BC03 — ignition/ACC state in data bytes after 62BC03
    # BC03 returns 8 data bytes: FD EE 3C 73 xx yy zz zz
    # Data byte 4 (0-indexed): 0x00=off, 0x20=ACC (bit 5), 0x0A=fob-wake
    # Data byte 5: 0x00=off, 0x60=ACC+IGN1
    igpm_resp = await terminal.send_command("22BC03", timeout=5.0)
    # Strip multi-frame prefixes (0:, 1:, etc.) and whitespace
    import re

    igpm_clean = (
        re.sub(r"\d+:", "", igpm_resp).replace(" ", "").replace("\n", "").upper()
    )

    verified = False
    if "62BC03" in igpm_clean:
        hex_start = igpm_clean.index("62BC03") + 6  # skip 62BC03
        data_hex = igpm_clean[hex_start:]
        if len(data_hex) >= 12:  # need at least 6 bytes (B0-B5)
            ign_byte = int(data_hex[8:10], 16)  # data byte 4
            ign2_byte = int(data_hex[10:12], 16)  # data byte 5
            if ign_byte & 0x20:  # bit 5 = ACC
                verified = True
                print(
                    f"        Ignition byte: 0x{ign_byte:02X} (B5=0x{ign2_byte:02X}) -- relay CONFIRMED engaged!"
                )
            else:
                print(
                    f"        Ignition byte: 0x{ign_byte:02X} (B5=0x{ign2_byte:02X}) -- relay did NOT engage!"
                )
                print(f"        The keyfob is likely not nearby.")
                print(
                    f"        SKM responds positively but the relay requires fob proximity."
                )
        else:
            print(f"        BC03 response too short to extract ignition byte.")
            print(f"        Got {len(data_hex) // 2} data bytes, need 12.")
            print(f"        Raw response: {igpm_resp.strip()}")
    else:
        print(f"        Could not read IGPM BC03: {igpm_resp.strip()}")

    if verified:
        print(f"\n  {desc} activated and verified! ECUs should now respond to queries.")
    else:
        print(f"\n  WARNING: {desc} command accepted but relay NOT confirmed.")
        print(f"  Bring the keyfob closer and try again.")

    return verified
