"""The diagnostic protocol the registry records for an ECU."""

from collections.abc import Mapping

from canair.ecus.names import resolve_tx
from canair.ecus.registry import EcuRegistryEntry, load_ecus
from canair.profile.model import Profile
from canair.uds.protocol import KWP, DiagProtocol, classify_protocol


def ecu_protocol(
    tx_id: int, ecus: Mapping[int, EcuRegistryEntry] | None = None, *, profile: Profile
) -> DiagProtocol:
    """The diagnostic protocol the registry records for a TX ID.

    :data:`~canair.uds.protocol.UNKNOWN` when the ECU is not in the registry or its
    ``id_protocol`` is missing, ``none`` or ``unknown``; see
    :func:`~canair.uds.protocol.classify_protocol`.
    """
    if ecus is None:
        ecus = load_ecus(profile)
    info = ecus.get(tx_id)
    return classify_protocol(info.get("id_protocol") if info else None)


def split_ecus_by_protocol(names: list[str], *, profile: Profile) -> tuple[list[str], list[str]]:
    """Partition ECU names into (uds, kwp) by their registry ``id_protocol``.

    KWP2000 ECUs (BMS/VCU/MCU/AAF) need the KWP variants of the discovery
    scanners: InputOutputControlByLocalIdentifier (0x30) instead of UDS 0x2F, and
    RequestRoutineResultsByLocalIdentifier (0x33) instead of UDS RoutineControl
    (0x31) — sending 0x31 to a KWP2000 ECU means StartRoutine and can actuate, so
    this split is a safety boundary, not just a convenience. ECUs whose protocol
    can't be resolved fall in the UDS bucket (the historical default).

    Lives in the registry module, not the CLI: it is a pure ``id_protocol``
    lookup, and being a *safety* boundary it should not sit in a command helper
    where a caller might reimplement it.
    """
    uds: list[str] = []
    kwp: list[str] = []
    for name in names:
        tx_id = resolve_tx(name, profile=profile)
        if tx_id is not None and ecu_protocol(tx_id, profile=profile) == KWP:
            kwp.append(name)
        else:
            uds.append(name)
    return uds, kwp
