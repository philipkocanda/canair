"""Tests for canlib.uds.protocol and the registry lookup every feature classifies through."""

from typing import Any, cast

import pytest

from canlib.ecus import EcuRegistryEntry, ecu_protocol
from canlib.uds.protocol import KWP, UDS, UNKNOWN, classify_protocol


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("UDS", UDS),
        ("uds", UDS),
        (" UDS ", UDS),
        ("KWP2000", KWP),
        ("kwp", KWP),
        ("none", UNKNOWN),
        ("unknown", UNKNOWN),
        ("", UNKNOWN),
        (None, UNKNOWN),
        ("UDS/KWP2000", UNKNOWN),
    ],
)
def test_classify_protocol(value, expected):
    assert classify_protocol(value) == expected


def _registry(**protocols: Any) -> dict[int, EcuRegistryEntry]:
    reg: dict[int, Any] = {}
    for i, (name, proto) in enumerate(protocols.items()):
        entry: dict[str, Any] = {"name": name}
        if proto is not None:
            entry["id_protocol"] = proto
        reg[0x7E0 + i] = entry
    return cast(dict[int, EcuRegistryEntry], reg)


def test_ecu_protocol_classifies_the_registry_value():
    reg = _registry(BMS="KWP2000", BCM="UDS", AMP="none", SRS=None)
    assert ecu_protocol(0x7E0, reg) == KWP
    assert ecu_protocol(0x7E1, reg) == UDS
    assert ecu_protocol(0x7E2, reg) == UNKNOWN
    assert ecu_protocol(0x7E3, reg) == UNKNOWN


def test_an_unregistered_ecu_is_unknown():
    assert ecu_protocol(0x123, _registry()) == UNKNOWN


def test_dtc_and_identity_resolve_through_the_registry(monkeypatch):
    from canlib.modes import identity_decode
    from canlib.modes.dtc import codes

    monkeypatch.setattr("canlib.ecus.ecu_protocol", lambda tx: UNKNOWN)
    monkeypatch.setattr(identity_decode, "ecu_protocol", lambda tx: UNKNOWN)
    # An unknown ECU reads DTCs over UDS (the historical default) but probes identity.
    assert codes.resolve_protocol("auto", 0x7E0) == UDS
    assert identity_decode.resolve_protocol_hint(0x7E0, "auto") is None

    monkeypatch.setattr("canlib.ecus.ecu_protocol", lambda tx: KWP)
    monkeypatch.setattr(identity_decode, "ecu_protocol", lambda tx: KWP)
    assert codes.resolve_protocol("auto", 0x7E0) == KWP
    assert identity_decode.resolve_protocol_hint(0x7E0, "auto") == KWP
