"""Which ECU resets canair sends, and the grant that lets ``canair reset`` send one.

ECUReset (``0x11``) is in the command blocklist (:mod:`canair.safety.blocklist`), so ``raw``,
``read`` and profile actions refuse it. ``canair reset`` sends one reset to one ECU. For the
length of that send it adds an exact grant to the terminal's
:class:`~canair.safety.blocklist.SessionPolicy`, and the blocklist lets a granted request
through only when :func:`reset_block` passes it (:func:`reset_granted`). The rule:

- Only the three types that restart an ECU are sent (:data:`RESET_TYPES`). ``04``/``05``
  change how the ECU powers down, and ``40``-``7E`` are the manufacturer's and supplier's,
  so canair does not know what they do.
- A KWP2000 ECU, and an ECU whose protocol the profile does not record, get only ``01``.
  ISO 14230-3 calls it powerOn, which restarts the ECU as UDS hardReset does; canair has not
  checked what the other values do on KWP2000.
- The suppress-positive-response bit is refused: the reply is how the user learns that the
  ECU took the reset.
- Never on a functional id: one frame would restart every ECU that hears it.

Whether ``canair reset`` may run at all is the user's ``safety.allow_reset`` config key
(:func:`canair.config.file.reset_allowed`), checked by the command.
"""

from __future__ import annotations

from typing import Final, Protocol

from canair.uds.addressing import FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID
from canair.uds.protocol import KWP, UDS, DiagProtocol
from canair.uds.services import ECU_RESET
from canair.uds.subfunctions import EcuResetType, subfunction_name

#: The reset types canair sends, by the name ``canair reset`` takes.
RESET_TYPES: Final[dict[str, EcuResetType]] = {
    "hard": EcuResetType.HARD_RESET,
    "key-off-on": EcuResetType.KEY_OFF_ON_RESET,
    "soft": EcuResetType.SOFT_RESET,
}
_NON_UDS_TYPES: Final = frozenset({EcuResetType.HARD_RESET})


class Grants(Protocol):
    """What :func:`reset_granted` reads of a :class:`~canair.safety.blocklist.SessionPolicy`."""

    def is_granted(self, request: str, tx_id: int | None) -> bool: ...

    def protocol(self, tx_id: int | None) -> DiagProtocol: ...


def reset_type_name(reset_type: int) -> str:
    """The standard's name for ``reset_type`` (``hardReset``), else its hex."""
    return subfunction_name(ECU_RESET, reset_type) or f"0x{reset_type:02X}"


def reset_block(reset_type: int, protocol: DiagProtocol, tx_id: int | None) -> str | None:
    """Why ``11 {reset_type}`` must not go to ``tx_id`` (an ECU speaking ``protocol``), or None."""
    name = reset_type_name(reset_type)
    if tx_id is None or tx_id in (FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID):
        return (
            "BLOCKED: ECUReset on a functional id would restart every ECU that hears it; "
            "canair resets one ECU at a time"
        )
    if reset_type not in RESET_TYPES.values():
        return (
            f"BLOCKED: ECUReset 0x{reset_type:02X} ({name}) does not restart the ECU, or its "
            f"effect is manufacturer-defined; canair sends only {_type_list()}"
        )
    if protocol != UDS and reset_type not in _NON_UDS_TYPES:
        speaks = "speaks KWP2000" if protocol == KWP else "has no protocol in the profile"
        return (
            f"BLOCKED: ECUReset 0x{reset_type:02X} ({name}) is a UDS reset type, and ECU "
            f"0x{tx_id:03X} {speaks}; only hard (11 01, powerOn on KWP2000) is sent to it"
        )
    return None


def reset_granted(request: str, policy: Grants, tx_id: int | None) -> bool:
    """Whether ``request`` is an ECU reset ``policy`` grants to ``tx_id`` and the rule permits.

    ``request`` is compacted upper-case hex. An odd length is an ELM327 request plus its
    expected-frame-count digit, which never reaches the bus.
    """
    body = request[:-1] if len(request) % 2 else request
    if len(body) != 4 or int(body[:2], 16) != ECU_RESET or not policy.is_granted(body, tx_id):
        return False
    return reset_block(int(body[2:], 16), policy.protocol(tx_id), tx_id) is None


def _type_list() -> str:
    return ", ".join(f"{word} (11 {value:02X})" for word, value in RESET_TYPES.items())
