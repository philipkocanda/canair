"""Which diagnostic protocol an ECU speaks — the one vocabulary and its one classifier.

A profile records an ECU's protocol as ``identity.id_protocol`` text, one of
:data:`ID_PROTOCOL_VALUES` (the ECU schema's ``identity.id_protocol`` enum). Every
protocol-dependent decision — the session and routine blocklist, NRC names, IOControl
release semantics, the DTC/identity/routine/IOControl service choice — reads it through
:func:`classify_protocol`, so the ``"KWP2000"`` → ``kwp`` mapping cannot drift between
features. Code that branches on protocol compares against :data:`UDS`/:data:`KWP`, never
against the raw profile string.

OBD-II (SAE J1979) is a set of request *services* an ECU may also answer, not a
protocol an ECU is classified as; its facts live in :mod:`canair.uds.obd` and the
service registry :mod:`canair.uds.services`.
"""

from __future__ import annotations

from typing import Final, Literal

DiagProtocol = Literal["uds", "kwp", "unknown"]
UDS: Final[DiagProtocol] = "uds"
KWP: Final[DiagProtocol] = "kwp"
UNKNOWN: Final[DiagProtocol] = "unknown"

# What `identity.id_protocol` may say: UDS (ISO 14229), KWP2000 (ISO 14230), none (answers
# session control, exposes no identity) and unknown (not yet determined).
ID_PROTOCOL_VALUES: Final = ("UDS", "KWP2000", "none", "unknown")


def classify_protocol(id_protocol: object) -> DiagProtocol:
    """Classify a profile ``identity.id_protocol`` value (or a ``uds``/``kwp`` flag value).

    Anything that is not recognisably UDS or KWP2000 (``none``, ``unknown``, missing) is
    :data:`UNKNOWN`; each caller decides what an unknown protocol means for it, and the
    safety rules always take the stricter reading.
    """
    text = str(id_protocol or "").strip().upper()
    if text.startswith("KWP"):
        return KWP
    if text == "UDS":
        return UDS
    return UNKNOWN


def id_protocol_value(protocol: DiagProtocol) -> str:
    """The ``identity.id_protocol`` text a profile records for ``protocol`` (``UDS``, ``KWP2000``).

    The inverse of :func:`classify_protocol` for the two known protocols; raises ``ValueError``
    for :data:`UNKNOWN`, which a profile writes as nothing at all.
    """
    if protocol == UDS:
        return "UDS"
    if protocol == KWP:
        return "KWP2000"
    raise ValueError(f"no id_protocol value for protocol {protocol!r}")
