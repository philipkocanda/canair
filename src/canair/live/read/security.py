"""One confirmed SecurityAccess exchange in an already-open diagnostic session."""

from __future__ import annotations

import asyncio
from pathlib import Path

from canair.live.read.security_provider import (
    ProviderError,
    check_key_file,
    check_provider,
    compute_key,
    read_key_file,
)
from canair.transport.session_manager import SessionManager
from canair.uds.addressing import FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID
from canair.uds.protocol import KWP, UDS, DiagProtocol
from canair.uds.response import UdsResponse
from canair.uds.services import SECURITY_ACCESS, positive_response_sid

PROVIDER_KEEPALIVE_INTERVAL = 1.0


def _failure(stage: str, response: UdsResponse) -> ProviderError:
    nrc = response.get("nrc")
    if isinstance(nrc, int):
        return ProviderError(f"security {stage} rejected: NRC 0x{nrc:02X}")
    return ProviderError(f"security {stage} failed or its outcome is unknown")


async def _provider_key(
    sm: SessionManager,
    tx_id: int,
    provider: Path,
    *,
    protocol: DiagProtocol,
    ecu: str,
    level: int,
    seed: bytes,
    max_key_bytes: int,
) -> bytes:
    """Keep the ECU alive without cancelling a keepalive halfway through an exchange."""
    task = asyncio.create_task(
        compute_key(
            provider,
            tx_id=tx_id,
            protocol=protocol,
            ecu=ecu,
            level=level,
            seed=seed,
            max_key_bytes=max_key_bytes,
        )
    )
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=PROVIDER_KEEPALIVE_INTERVAL)
            if done:
                return task.result()
            if not sm.table.wants_keepalive(tx_id):
                continue
            # An ECU with no TesterPresent refuses the first one and is never asked again.
            if not await sm.send_keepalive(tx_id, sensitive=True) and sm.table.wants_keepalive(
                tx_id
            ):
                raise ProviderError("session keepalive failed before security key submission")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def unlock(
    sm: SessionManager,
    *,
    ecu: str,
    tx_id: int,
    level: int,
    provider: str | None,
    key_file: str | None,
) -> str:
    """Send one challenge and at most one response; failures never expose their payloads."""
    terminal = sm.terminal
    if tx_id in (FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID):
        raise ProviderError("SecurityAccess requires a physical ECU target")
    if not 1 <= level <= 0xFD or not level & 1:
        raise ProviderError("security level must be an odd byte from 01 to FD")
    if bool(provider) == bool(key_file):
        raise ProviderError("select exactly one security provider or key file")
    provider_path = check_provider(provider) if provider else None
    if key_file:
        check_key_file(key_file)
    protocol = terminal.session_policy.protocol(tx_id)
    if protocol != KWP and level >= 0x80:
        raise ProviderError("high security levels require a known KWP2000 ECU")
    if sm.session_mode(tx_id) is None:
        raise ProviderError("SecurityAccess requires an accepted session on the selected ECU")
    request = bytes([SECURITY_ACCESS, level]).hex().upper()
    sm.security_seen = True
    sm.security_targets.add(tx_id)
    async with terminal.transaction():
        await terminal.set_header(tx_id)
        response = await terminal.send_uds(
            request, expected_sid=SECURITY_ACCESS, expected_echo=bytes([level]), retries=0
        )
    if not response.get("ok"):
        raise _failure("seed request", response)
    data = response.get("bytes") or b""
    if len(data) < 3 or data[0] != positive_response_sid(SECURITY_ACCESS) or data[1] != level:
        raise ProviderError("security seed response was invalid")
    seed = bytes(data[2:])
    sm.mark_active(tx_id)
    if protocol == UDS and not any(seed):
        return "already unlocked"
    if provider_path is not None:
        key = await _provider_key(
            sm,
            tx_id,
            provider_path,
            protocol=protocol,
            ecu=ecu,
            level=level,
            seed=seed,
            max_key_bytes=terminal.max_request_bytes - 2,
        )
    elif key_file:
        key = read_key_file(key_file, terminal.max_request_bytes - 2)
    else:
        raise ProviderError("no security provider or key file selected")
    if sm.link_error is not None:
        raise ProviderError("session connection was lost before security key submission")
    if not sm.table.has(tx_id):
        raise ProviderError("session was closed before security key submission")

    key_request = bytes([SECURITY_ACCESS, level + 1]) + key
    text = key_request.hex().upper()
    async with terminal.transaction():
        await terminal.set_header(tx_id)
        policy = terminal.session_policy
        terminal.session_policy = policy.granting_security(tx_id, text)
        try:
            result = await terminal.send_uds(
                text, expected_sid=SECURITY_ACCESS, expected_echo=bytes([level + 1]), retries=0
            )
        finally:
            terminal.session_policy = policy
    if not result.get("ok"):
        # A timeout can mean the key was accepted. Do not retry or continue the pipeline.
        raise _failure("key submission", result)
    data = result.get("bytes") or b""
    if len(data) < 2 or data[0] != positive_response_sid(SECURITY_ACCESS) or data[1] != level + 1:
        raise ProviderError("security key response was invalid; its outcome is unknown")
    sm.mark_active(tx_id)
    return "accepted"
