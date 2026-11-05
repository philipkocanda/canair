"""Wake sleeping ECUs via SKM relay control.

Ioniq/HKMC-specific: the Smart Key Module relay DIDs (``B108``–``B10B``), the
magic IOControl bytes (``0A0A05``), and the ECU addresses (SKM ``0x7A5``, IGPM
``0x770``, BCM ``0x7A0``) are all Ioniq-particular, so the ``skm-wake`` command
is gated behind the profile ``skm_wakeup`` capability (see
:mod:`canair.safety.quirks`) — it is refused for profiles that don't declare it.

This module is the *relay-actuation* extension (idea 3): rouse the SKM, then
close a power relay (ACC/IGN). Merely *reading* a fast-sleeping ECU is the
make-neutral, profile-declared per-ECU ``wake:`` block in :mod:`canair.ecus.wake`
(idea 1), which uses the same rapid-fire prime primitive
(:meth:`canair.transport.session_manager.SessionManager.rapid_read_wake`) but needs no
quirk. Extracting these relay DIDs/addresses into the profile is future work.
"""

import asyncio
import re
import time

from canair.core.prompt import typed_confirm
from canair.render import messages
from canair.transport.elm327.terminal import Elm327Terminal
from canair.uds.nrc import (
    NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION,
    is_negative_response,
    is_response_pending,
)
from canair.uds.parse import reply_lines
from canair.uds.requests import io_control_request
from canair.uds.services import (
    DEFAULT_SESSION_REQUEST,
    EXTENDED_SESSION_REQUEST,
    INPUT_OUTPUT_CONTROL_BY_IDENTIFIER,
    positive_response_sid,
)
from canair.uds.subfunctions import IoControlParameter

# SKM relay control DIDs
SKM_RELAYS = {
    "acc": ("B108", "ACC (Accessory)"),
    "ign1": ("B109", "IGN1 (Ignition 1)"),
    "ign2": ("B10A", "IGN2 (Ignition 2)"),
    "start": ("B10B", "Start Relay"),
}

# Magic bytes required for SKM IOControl ON
SKM_MAGIC = bytes.fromhex("0A0A05")

_IO_CONTROL = INPUT_OUTPUT_CONTROL_BY_IDENTIFIER


def _answered(text: str, did: str | None = None) -> bool:
    """Does the adapter text hold a positive IOControl reply (for ``did``'s high byte)?"""
    head = bytes([positive_response_sid(_IO_CONTROL)])
    if did is not None:
        head += bytes.fromhex(did[:2])
    return any(r.startswith(head) for r in reply_lines(text))


def _pending(text: str) -> bool:
    """Does the adapter text hold an IOControl ResponsePending (``7F 2F 78``)?"""
    return any(is_response_pending(r, _IO_CONTROL) for r in reply_lines(text))


def _refused(text: str, nrc: int | None = None) -> bool:
    """Does the adapter text hold an IOControl refusal (``7F 2F <nrc>``), not a pending one?"""
    return any(
        is_negative_response(r, _IO_CONTROL, nrc) and not is_response_pending(r)
        for r in reply_lines(text)
    )


def parse_igpm_bc03(raw_response: str) -> dict:
    """Parse IGPM BC03 response for ignition/ACC state.

    Returns dict with:
        ok: bool - whether parsing succeeded
        acc: bool - ACC relay engaged (bit 5 of data byte 4)
        ign_byte: int - raw data byte 4 value
        ign2_byte: int - raw data byte 5 value
        error: str - error message if parsing failed
    """
    cleaned = re.sub(r"\d+:", "", raw_response).replace(" ", "").replace("\n", "").upper()

    if "62BC03" not in cleaned:
        return {
            "ok": False,
            "acc": False,
            "error": f"No 62BC03 in response: {raw_response.strip()}",
        }

    hex_start = cleaned.index("62BC03") + 6
    data_hex = cleaned[hex_start:]

    if len(data_hex) < 12:
        return {
            "ok": False,
            "acc": False,
            "error": f"Too short ({len(data_hex) // 2} data bytes, need 6)",
        }

    ign_byte = int(data_hex[8:10], 16)  # data byte 4
    ign2_byte = int(data_hex[10:12], 16)  # data byte 5
    acc = bool(ign_byte & 0x20)  # bit 5 = ACC

    return {"ok": True, "acc": acc, "ign_byte": ign_byte, "ign2_byte": ign2_byte}


def parse_bcm_b003(raw_response: str) -> dict:
    """Parse BCM B003 response for power mode byte.

    Returns dict with:
        ok: bool - whether parsing succeeded
        power_byte: int - raw data byte 6 value (stripped, 0-indexed)
        state: str - human-readable state label
        error: str - error message if parsing failed
    """
    cleaned = re.sub(r"\d+:", "", raw_response).replace(" ", "").replace("\n", "").upper()

    if "62B003" not in cleaned:
        return {"ok": False, "error": f"No 62B003 in response: {raw_response.strip()}"}

    hex_start = cleaned.index("62B003") + 6
    data_hex = cleaned[hex_start:]

    if len(data_hex) < 14:
        return {
            "ok": False,
            "error": f"Too short ({len(data_hex) // 2} data bytes, need 7)",
        }

    pwr_byte = int(data_hex[12:14], 16)  # data byte 6 (stripped, 0-indexed)
    known = {0x09: "ACC?", 0x0A: "ACC+IGN1?", 0xF5: "OFF/sleep?"}
    state = known.get(pwr_byte, "unknown")

    return {"ok": True, "power_byte": pwr_byte, "state": state}


async def wake_and_read(terminal: Elm327Terminal, tx_id: str, pid: str) -> dict:
    """Wake an ECU, enter extended session, and read a PID.

    Args:
        terminal: WiCAN terminal connection
        tx_id: ECU TX ID as hex string (e.g. "770")
        pid: PID to read (e.g. "22BC03")

    Returns dict with:
        ok: bool - whether the read succeeded
        response: str - raw response string (if ok)
        error: str - error description (if not ok)
    """
    # One transaction, so nothing can re-point the header between the requests.
    async with terminal.transaction():
        await terminal.set_header(int(tx_id, 16))

        # Wake from deep sleep
        await terminal.send_command(DEFAULT_SESSION_REQUEST, timeout=3.0)

        # Extended session
        resp = await terminal.send_command(EXTENDED_SESSION_REQUEST, timeout=3.0)
        if "5003" not in resp.replace(" ", "") and "50 03" not in resp:
            return {"ok": False, "error": "Could not enter extended session."}

        # Read PID
        resp = await terminal.send_command(pid, timeout=5.0)
    return {"ok": True, "response": resp}


async def mode_skm_wakeup(terminal: Elm327Terminal, level: str, verbose: bool):
    """Wake sleeping ECUs via SKM relay control.

    Sends a broadcast 3E00 + 1001 to nudge the SKM awake, then establishes
    an extended diagnostic session and activates the requested relay level.
    """
    if level not in SKM_RELAYS:
        messages.error(f"unknown level: {level}. Available: {', '.join(SKM_RELAYS)}")
        return False

    did, desc = SKM_RELAYS[level]

    if level == "start":
        messages.warning("the start relay can crank the motor")
        proceed = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: typed_confirm(
                "Only proceed if the car is in Park and conditions are safe.", "YES"
            ),
        )
        if not proceed:
            raise SystemExit(1)

    print(f"\n  SKM Wakeup -- {desc}")
    print("  ---------------------------------")

    # Step 1: Rapid-fire 1001 to wake SKM transceiver from deep sleep
    # SKM wakes on the 2nd frame but has a ~2s sleep timer — needs fast CAN
    # traffic (64ms timeout) to stay awake. Direct to 0x7A5, no broadcast needed.
    print("  [1/4] Waking SKM (rapid-fire 1001 at 64ms timeout)…")
    await terminal.set_header(0x7A5)

    # Save current timeout and switch to fast mode
    await terminal.send_command("ATST10")  # 64ms timeout

    skm_awake = False
    for attempt in range(10):
        resp = await terminal.send_command(DEFAULT_SESSION_REQUEST, timeout=3.0)
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
        messages.error(
            "SKM did not wake after 10 rapid-fire attempts. "
            "The SKM transceiver may be fully unpowered."
        )
        return False

    print(f"        SKM awake (attempt {attempt + 1}).")

    # Step 2: Extended diagnostic session — send immediately while SKM is awake
    print("  [2/4] Establishing extended session on SKM (0x7A5)…")

    session_ok = False
    for attempt in range(5):
        resp = await terminal.send_command(EXTENDED_SESSION_REQUEST, timeout=3.0)
        if "50 03" in resp or "5003" in resp:
            session_ok = True
            break
        if verbose:
            print(f"        1003 -> {resp} (attempt {attempt + 1})")

    # Restore user's timeout
    await terminal.send_command(terminal.elm_timeout_cmd)

    if not session_ok:
        messages.error("SKM awake but extended session failed.")
        return False

    print("        Extended session (10 03) established.")

    # Step 3: Send relay ON command
    await terminal.drain()
    cmd = io_control_request(int(did, 16), IoControlParameter.SHORT_TERM_ADJUSTMENT, SKM_MAGIC)
    print(f"  [3/4] Sending {desc} ON ({cmd})…")
    resp = await terminal.send_command(cmd, timeout=10.0)

    clean = resp.replace(" ", "").replace("\n", "").upper()
    is_fc_only = clean in ("F00", "FC00", "F0", "FC0") or (
        len(clean) <= 4 and clean.startswith("F")
    )

    if is_fc_only or (_pending(resp) and not _answered(resp)):
        if verbose:
            reason = "FC echo" if is_fc_only else "pending NRC"
            print(f"        Initial response: {resp.strip()} ({reason})")
            print("        Waiting for UDS response…")
        deadline = time.monotonic() + 10.0
        extra_parts = []
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                # Passively collect late frames through the terminal's decoded
                # receive (channel unwraps any WebSocket envelope); None = timeout.
                msg = await terminal.recv_frame(min(remaining, 1.0))
            except Exception:
                break
            if msg is None:
                combined = "".join(extra_parts)
                if _pending(combined) and not _answered(combined):
                    continue
                break
            if msg:
                extra_parts.append(msg)
                if verbose:
                    print(f"        Recv: {msg.strip()!r}")
            combined = "".join(extra_parts)
            if _answered(combined) or _refused(combined):
                break
        if extra_parts:
            resp = resp + "\n" + "".join(extra_parts)
            if verbose:
                print(f"        Full response: {resp.strip()}")

    if not _answered(resp, did):
        if _refused(resp, NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION):
            messages.error(
                "serviceNotSupportedInActiveSession. "
                "The extended session may have expired. Try again."
            )
        elif _pending(resp):
            messages.warning("pending response received but no positive confirmation")
        else:
            messages.error(f"could not confirm relay activation (response: {resp})")
        return False

    print("        UDS positive response received.")

    # Step 4: Verify ACC actually engaged by reading IGPM and BCM
    # SKM returns positive UDS response even without keyfob nearby,
    # but the relay physically doesn't close. Read both ECUs to verify.
    print("  [4/4] Verifying relay engagement…")

    # Small delay for relay state to settle
    await asyncio.sleep(0.3)

    verified = False

    # --- IGPM BC03: ignition/ACC state ---
    print("        IGPM (0x770) BC03…")
    igpm = await wake_and_read(terminal, "770", "22BC03")
    if igpm["ok"]:
        result = parse_igpm_bc03(igpm["response"])
        if result["ok"]:
            if result["acc"]:
                verified = True
                print(
                    f"          B4=0x{result['ign_byte']:02X} B5=0x{result['ign2_byte']:02X} — ACC CONFIRMED"
                )
            else:
                print(
                    f"          B4=0x{result['ign_byte']:02X} B5=0x{result['ign2_byte']:02X} — no ACC"
                )
        else:
            print(f"          {result['error']}")
    else:
        print(f"          {igpm['error']}")

    # --- BCM B003: power mode byte ---
    print("        BCM  (0x7A0) B003…")
    bcm = await wake_and_read(terminal, "7A0", "22B003")
    if bcm["ok"]:
        result = parse_bcm_b003(bcm["response"])
        if result["ok"]:
            print(f"          Byte 6=0x{result['power_byte']:02X} ({result['state']})")
        else:
            print(f"          {result['error']}")
    else:
        print(f"          {bcm['error']}")

    if verified:
        print(f"\n  {desc} activated and verified!")
    else:
        messages.warning(
            f"{desc} command accepted but the relay is NOT confirmed. "
            "The keyfob is likely not nearby."
        )

    return verified
