"""Invoke an explicitly selected external SecurityAccess provider.

The child is trusted user code, not a sandbox. Its output and errors must never be rendered or
logged: both can contain the seed or key. No algorithm or key belongs in canair.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import stat
from pathlib import Path

from canair.uds.protocol import DiagProtocol
from canair.uds.security import SecurityAccessError

MAX_OUTPUT = 4096
PROVIDER_TIMEOUT = 3.0


ProviderError = SecurityAccessError


def check_provider(path: str) -> Path:
    """Check an executable before any diagnostic requests are sent."""
    executable = Path(path).expanduser().resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ProviderError("security provider is not an executable file")
    return executable


def check_key_file(path: str) -> Path:
    """Require an owner-only regular key file before opening a connection."""
    key_file = Path(path).expanduser().resolve()
    try:
        info = key_file.stat()
    except OSError:
        raise ProviderError("security key file is unavailable") from None
    if (
        not key_file.is_file()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or not info.st_mode & stat.S_IRUSR
    ):
        raise ProviderError("security key file must be a regular file readable only by its owner")
    if not 0 < info.st_size <= MAX_OUTPUT:
        raise ProviderError("security key file has an invalid length")
    return key_file


def read_key_file(path: str, max_key_bytes: int) -> bytes:
    """Read at most the transport limit, checking the opened file instead of a stale stat."""
    key_file = check_key_file(path)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(key_file, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ProviderError("security key file permissions changed")
            key = stream.read(max_key_bytes + 1)
    except OSError:
        raise ProviderError("could not read security key file") from None
    if not 0 < len(key) <= max_key_bytes:
        raise ProviderError("security key file has an invalid length for this transport")
    return key


async def _bounded_read(stream: asyncio.StreamReader) -> bytes:
    data = bytearray()
    while chunk := await stream.read(1024):
        data.extend(chunk)
        if len(data) > MAX_OUTPUT:
            raise ProviderError("security provider output exceeded the limit")
    return bytes(data)


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate provider response field")
    return result


async def _stop(process: asyncio.subprocess.Process) -> None:
    # A child may exit while a descendant still holds its output pipes open.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


async def compute_key(
    provider: Path,
    *,
    protocol: DiagProtocol,
    ecu: str,
    tx_id: int,
    level: int,
    seed: bytes,
    max_key_bytes: int,
) -> bytes:
    """Call a provider once with a bounded JSON exchange; never expose its output on failure."""
    request = (
        json.dumps(
            {
                "version": 1,
                "protocol": protocol,
                "ecu": ecu,
                "tx_id": f"{tx_id:X}",
                "level": f"{level:02X}",
                "seed": seed.hex().upper(),
            }
        ).encode()
        + b"\n"
    )
    env = {
        name: os.environ[name]
        for name in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
        if name in os.environ
    }
    try:
        process = await asyncio.create_subprocess_exec(
            str(provider),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=env,
        )
    except OSError:
        raise ProviderError("could not start security provider") from None
    stdin, out, err = process.stdin, process.stdout, process.stderr
    assert stdin and out and err

    async def exchange() -> tuple[bytes, bytes]:
        stdin.write(request)
        await stdin.drain()
        stdin.close()
        stdout, stderr, _ = await asyncio.gather(
            _bounded_read(out), _bounded_read(err), process.wait()
        )
        return stdout, stderr

    try:
        stdout, _stderr = await asyncio.wait_for(exchange(), PROVIDER_TIMEOUT)
        if process.returncode != 0:
            raise ProviderError("security provider failed")
        try:
            result = json.loads(stdout, object_pairs_hook=_unique_fields)
            if not isinstance(result, dict) or set(result) != {"version", "key"}:
                raise ValueError
            if type(result["version"]) is not int or result["version"] != 1:
                raise ValueError
            if not isinstance(result["key"], str):
                raise ValueError
            text = result["key"]
            if not text or len(text) % 2 or any(c not in "0123456789abcdefABCDEF" for c in text):
                raise ValueError
            key = bytes.fromhex(text)
            if len(key) > max_key_bytes:
                raise ValueError
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ProviderError("security provider returned an invalid response") from None
        return key
    except TimeoutError:
        raise ProviderError("security provider timed out") from None
    except OSError:
        raise ProviderError("security provider communication failed") from None
    finally:
        await _stop(process)
