"""Which diagnostic protocol an ECU speaks — the one vocabulary and its one classifier.

A profile records an ECU's protocol as free-form ``identity.id_protocol`` text (``UDS``,
``KWP2000``, ``none``, ``unknown``; see ``valid_id_protocols`` in the pids schema). Every
protocol-dependent decision — the session and routine blocklist, NRC names, IOControl
release semantics, the DTC/identity/routine/IOControl service choice — reads it through
:func:`classify_protocol`, so the ``"KWP2000"`` → ``kwp`` mapping cannot drift between
features. Code that branches on protocol compares against :data:`UDS`/:data:`KWP`, never
against the raw profile string.

OBD-II (SAE J1979) is a set of request *services* an ECU may also answer, not a
protocol an ECU is classified as; its facts live in :mod:`canlib.uds.obd` and the
service registry :mod:`canlib.uds.services`.
"""

from __future__ import annotations

from typing import Final, Literal

DiagProtocol = Literal["uds", "kwp", "unknown"]
UDS: Final[DiagProtocol] = "uds"
KWP: Final[DiagProtocol] = "kwp"
UNKNOWN: Final[DiagProtocol] = "unknown"


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
