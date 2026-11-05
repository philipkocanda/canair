"""ReadMemoryByAddress (service ``0x23``): the request, its reply and what an NRC means.

I/O-free. A memory read is the one diagnostic read whose reply repeats nothing of its
request: ``63`` and then exactly the bytes asked for. So the only check a reply can get
is its length, and both are fixed by the request (:func:`parse_memory_request`).

* **UDS** (ISO 14229-1): ``23 ALFID address size``. The addressAndLengthFormatIdentifier
  (ALFID) says how wide the two fields are: its high nibble is the size width, its low
  nibble the address width (``24`` = a 2-byte size and a 4-byte address).
* **KWP2000** (ISO 14230-3): ``23 address(3) size(1)``, fixed.

A request is self-describing on UDS (the ALFID) but not across protocols: a 5-byte
request is the KWP2000 form *and* the UDS form for ALFID ``12`` or ``21``. So a parse
takes the ECU's protocol, and with none it refuses the ambiguous case rather than guess.

The query token is ``23/ADDRESS+SIZE`` (both hex). It names an address and a size; the
ECU's ``memory.format`` turns it into a request (:func:`memory_request`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from canair.uds.nrc import (
    KWP_NRC_CANT_UPLOAD_FROM_SPECIFIED_ADDRESS,
    KWP_NRC_CANT_UPLOAD_NUMBER_OF_BYTES_REQUESTED,
    KWP_NRC_INCORRECT_BYTE_COUNT_DURING_BLOCK_TRANSFER,
    KWP_NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_DIAGNOSTIC_SESSION,
    NRC_CONDITIONS_NOT_CORRECT,
    NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT,
    NRC_REQUEST_OUT_OF_RANGE,
    NRC_RESPONSE_TOO_LONG,
    NRC_SECURITY_ACCESS_DENIED,
    NRC_SERVICE_NOT_SUPPORTED,
    NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION,
    NRC_SUB_FUNCTION_NOT_SUPPORTED,
)
from canair.uds.protocol import KWP, UDS, DiagProtocol
from canair.uds.services import READ_MEMORY_BY_ADDRESS

#: The largest memory read canair stores as a capture. A larger one is a dump, and dumps
#: stay on the machine (``canair memory dump``): they can hold OEM software and secrets.
MEMORY_SIGNAL_MAX_BYTES: Final = 64

#: The most a reply can carry: ISO-TP's 4095-byte message, less the ``63``.
MAX_READ_BYTES: Final = 4094

#: How a profile writes the KWP2000 form in ``memory.format``.
KWP_FORMAT_NAME: Final = "kwp"

#: The separator of the ``23/ADDRESS+SIZE`` token.
TOKEN_SERVICE: Final = f"{READ_MEMORY_BY_ADDRESS:02X}/"
_TOKEN_SIZE_SEP: Final = "+"

_MAX_ADDRESS_BYTES: Final = 8
_MAX_SIZE_BYTES: Final = 4
_KWP_ADDRESS_BYTES: Final = 3
_KWP_SIZE_BYTES: Final = 1


class MemoryRequestError(ValueError):
    """A memory format, request or token that cannot be built or read."""


@dataclass(frozen=True)
class MemoryFormat:
    """How wide an ECU's memory address and size fields are, and in which protocol form."""

    address_bytes: int
    size_bytes: int
    kwp: bool = False

    @property
    def alfid(self) -> int | None:
        """The UDS addressAndLengthFormatIdentifier, ``None`` for the KWP2000 form."""
        return None if self.kwp else (self.size_bytes << 4) | self.address_bytes

    @property
    def name(self) -> str:
        """The form a profile writes in ``memory.format``: ``"24"`` or ``"kwp"``."""
        return KWP_FORMAT_NAME if self.kwp else f"{self.alfid:02X}"

    @property
    def max_address(self) -> int:
        return (1 << (8 * self.address_bytes)) - 1

    @property
    def max_size(self) -> int:
        return min((1 << (8 * self.size_bytes)) - 1, MAX_READ_BYTES)

    @property
    def request_length(self) -> int:
        """Bytes in a request: SID, the ALFID on UDS, then the address and the size."""
        return 1 + (0 if self.kwp else 1) + self.address_bytes + self.size_bytes


KWP_FORMAT: Final = MemoryFormat(_KWP_ADDRESS_BYTES, _KWP_SIZE_BYTES, kwp=True)

#: The ALFIDs ``scan memory`` tries when an ECU's format is unknown, most common first.
#: ``13`` is the Ioniq HVAC's, ``24`` its BCM's.
PROBE_ALFIDS: Final = (0x44, 0x24, 0x14, 0x13, 0x23, 0x22, 0x12)


def format_from_alfid(alfid: int) -> MemoryFormat:
    """The format an ALFID names; raises :class:`MemoryRequestError` for one canair cannot use."""
    size_bytes, address_bytes = alfid >> 4, alfid & 0x0F
    if not 1 <= address_bytes <= _MAX_ADDRESS_BYTES or not 1 <= size_bytes <= _MAX_SIZE_BYTES:
        raise MemoryRequestError(
            f"ALFID {alfid:02X}: needs a 1-{_MAX_SIZE_BYTES} byte size and a "
            f"1-{_MAX_ADDRESS_BYTES} byte address"
        )
    return MemoryFormat(address_bytes, size_bytes)


def parse_format(value: Any) -> MemoryFormat:
    """A ``memory.format`` value: ``"kwp"`` or a two-digit hex ALFID (``"24"``)."""
    text = str(value).strip()
    if text.lower() == KWP_FORMAT_NAME:
        return KWP_FORMAT
    if len(text) != 2:
        raise MemoryRequestError(
            f"memory format {value!r}: write a two-digit hex ALFID (24) or {KWP_FORMAT_NAME}"
        )
    try:
        alfid = int(text, 16)
    except ValueError:
        raise MemoryRequestError(
            f"memory format {value!r}: write a two-digit hex ALFID (24) or {KWP_FORMAT_NAME}"
        ) from None
    return format_from_alfid(alfid)


def default_format(protocol: DiagProtocol | None) -> MemoryFormat | None:
    """The format a protocol fixes: KWP2000's, or ``None`` (a UDS ECU's must be found)."""
    return KWP_FORMAT if protocol == KWP else None


@dataclass(frozen=True)
class MemoryRead:
    """One memory read: ``size`` bytes from ``address`` in ``format``."""

    format: MemoryFormat
    address: int
    size: int

    @property
    def end(self) -> int:
        """The last address read."""
        return self.address + self.size - 1

    @property
    def reply_length(self) -> int:
        """A positive reply's length: the ``63`` and the bytes read."""
        return 1 + self.size

    @property
    def request(self) -> str:
        """The request as upper-case hex, the PID key a memory signal is defined under."""
        fmt = self.format
        head = (
            [READ_MEMORY_BY_ADDRESS] if fmt.alfid is None else [READ_MEMORY_BY_ADDRESS, fmt.alfid]
        )
        body = self.address.to_bytes(fmt.address_bytes, "big") + self.size.to_bytes(
            fmt.size_bytes, "big"
        )
        return (bytes(head) + body).hex().upper()

    @property
    def token(self) -> str:
        """The query token for this read (``23/0001A2B0+04``)."""
        width = 2 * self.format.address_bytes
        return f"{TOKEN_SERVICE}{self.address:0{width}X}{_TOKEN_SIZE_SEP}{self.size:02X}"


def memory_read(fmt: MemoryFormat, address: int, size: int) -> MemoryRead:
    """A checked :class:`MemoryRead`; raises :class:`MemoryRequestError` when it cannot be sent."""
    if size < 1 or size > fmt.max_size:
        raise MemoryRequestError(
            f"a memory read of {size} bytes does not fit format {fmt.name} (1-{fmt.max_size} bytes)"
        )
    if address < 0 or address > fmt.max_address:
        raise MemoryRequestError(
            f"address 0x{address:X} does not fit format {fmt.name} "
            f"({fmt.address_bytes}-byte addresses)"
        )
    if address + size - 1 > fmt.max_address:
        raise MemoryRequestError(
            f"a read of {size} bytes at 0x{address:X} runs past the address space"
        )
    return MemoryRead(fmt, address, size)


def memory_request(fmt: MemoryFormat, address: int, size: int) -> str:
    """The request hex for ``size`` bytes at ``address`` (see :func:`memory_read`)."""
    return memory_read(fmt, address, size).request


def _request_bytes(request: Any) -> bytes | None:
    text = "".join(str(request).split())
    if len(text) % 2:
        return None
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


def is_memory_request(request: Any) -> bool:
    """Whether ``request`` (hex, or a PID key) is a ReadMemoryByAddress request."""
    raw = _request_bytes(request)
    return bool(raw) and raw[0] == READ_MEMORY_BY_ADDRESS


def _uds_read(raw: bytes) -> MemoryRead | None:
    if len(raw) < 2:
        return None
    try:
        fmt = format_from_alfid(raw[1])
    except MemoryRequestError:
        return None
    if len(raw) != fmt.request_length:
        return None
    a = fmt.address_bytes
    address = int.from_bytes(raw[2 : 2 + a], "big")
    size = int.from_bytes(raw[2 + a :], "big")
    return MemoryRead(fmt, address, size) if size else None


def _kwp_read(raw: bytes) -> MemoryRead | None:
    if len(raw) != KWP_FORMAT.request_length:
        return None
    address = int.from_bytes(raw[1:4], "big")
    return MemoryRead(KWP_FORMAT, address, raw[4]) if raw[4] else None


def parse_memory_request(request: Any, protocol: DiagProtocol | None = None) -> MemoryRead | None:
    """The memory read a request (hex, or a PID key) asks for, or ``None`` if it is none.

    ``protocol`` picks the form. Without one a request both forms read, with a different
    size, is ``None``: the reply length it would be checked against is unknown.
    """
    raw = _request_bytes(request)
    if not raw or raw[0] != READ_MEMORY_BY_ADDRESS:
        return None
    if protocol == KWP:
        return _kwp_read(raw)
    if protocol == UDS:
        return _uds_read(raw)
    uds, kwp = _uds_read(raw), _kwp_read(raw)
    if uds is not None and kwp is not None:
        return uds if uds.size == kwp.size else None
    return uds or kwp


def parse_memory_token(token: str) -> tuple[int, int] | None:
    """``(address, size)`` of a ``23/ADDRESS+SIZE`` token, ``None`` if it is not one.

    Raises :class:`MemoryRequestError` for a token that starts ``23/`` but is malformed.
    """
    text = token.strip().upper()
    if not text.startswith(TOKEN_SERVICE):
        return None
    body = text[len(TOKEN_SERVICE) :]
    address_hex, sep, size_hex = body.partition(_TOKEN_SIZE_SEP)
    try:
        if not sep or not address_hex or not size_hex:
            raise ValueError
        address, size = int(address_hex, 16), int(size_hex, 16)
    except ValueError:
        raise MemoryRequestError(
            f"memory token {token!r}: write {TOKEN_SERVICE}ADDRESS{_TOKEN_SIZE_SEP}SIZE in hex, "
            f"e.g. {TOKEN_SERVICE}0001A2B0{_TOKEN_SIZE_SEP}04"
        ) from None
    if size < 1:
        raise MemoryRequestError(f"memory token {token!r}: the size must be at least 1 byte")
    return address, size


def token_names(token: str, request: Any) -> bool:
    """Whether a ``23/…`` token names the memory read ``request`` (any format)."""
    try:
        wanted = parse_memory_token(token)
    except MemoryRequestError:
        return False
    if wanted is None:
        return False
    raw = _request_bytes(request)
    if not raw:
        return False
    reads = [r for r in (_uds_read(raw), _kwp_read(raw)) if r is not None]
    return any((r.address, r.size) == wanted for r in reads)


def exceeds_signal_cap(request: Any) -> bool:
    """Whether a capture of ``request`` would be a memory read over the signal cap."""
    raw = _request_bytes(request)
    if not raw or raw[0] != READ_MEMORY_BY_ADDRESS:
        return False
    reads = [r for r in (_uds_read(raw), _kwp_read(raw)) if r is not None]
    # An unreadable 23 request is judged by its reply instead, which callers check.
    return any(r.size > MEMORY_SIGNAL_MAX_BYTES for r in reads)


class RegionResult(StrEnum):
    """What one memory read says about the address it asked for."""

    READABLE = "readable"
    OUT_OF_RANGE = "out_of_range"
    LOCKED = "locked"
    CONDITIONS = "conditions"
    WRONG_SESSION = "wrong_session"
    UNSUPPORTED = "unsupported"
    BAD_FORMAT = "bad_format"
    TOO_LARGE = "too_large"
    NO_ANSWER = "no_answer"
    REFUSED = "refused"


_NRC_RESULTS: Final[Mapping[int, RegionResult]] = {
    NRC_REQUEST_OUT_OF_RANGE: RegionResult.OUT_OF_RANGE,
    KWP_NRC_CANT_UPLOAD_FROM_SPECIFIED_ADDRESS: RegionResult.OUT_OF_RANGE,
    NRC_SECURITY_ACCESS_DENIED: RegionResult.LOCKED,
    NRC_CONDITIONS_NOT_CORRECT: RegionResult.CONDITIONS,
    NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION: RegionResult.WRONG_SESSION,
    KWP_NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_DIAGNOSTIC_SESSION: RegionResult.WRONG_SESSION,
    NRC_SERVICE_NOT_SUPPORTED: RegionResult.UNSUPPORTED,
    NRC_SUB_FUNCTION_NOT_SUPPORTED: RegionResult.BAD_FORMAT,
    NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT: RegionResult.BAD_FORMAT,
    KWP_NRC_INCORRECT_BYTE_COUNT_DURING_BLOCK_TRANSFER: RegionResult.BAD_FORMAT,
    NRC_RESPONSE_TOO_LONG: RegionResult.TOO_LARGE,
    KWP_NRC_CANT_UPLOAD_NUMBER_OF_BYTES_REQUESTED: RegionResult.TOO_LARGE,
}


def classify_memory_reply(response: Mapping[str, Any]) -> RegionResult:
    """What a parsed reply (:class:`~canair.uds.response.UdsResponse`) to a memory read means."""
    if response.get("ok"):
        return RegionResult.READABLE
    nrc = response.get("nrc")
    if nrc is None:
        return RegionResult.NO_ANSWER
    return _NRC_RESULTS.get(int(nrc), RegionResult.REFUSED)
