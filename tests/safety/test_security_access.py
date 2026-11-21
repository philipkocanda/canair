"""SecurityAccess is not an ordinary read and cannot be broadcast or sent without consent."""

import asyncio

import pytest

from canair.safety.actuation import ACTUATE, READ, classify
from canair.safety.blocklist import (
    SafetyBlocked,
    SessionPolicy,
    check_command_safety,
    enforce_command_safety,
    unliftable_block,
)
from canair.uds.protocol import KWP, UDS, UNKNOWN


def test_security_access_classification():
    assert classify("2701", UDS) == READ
    assert classify("27F1", KWP) == READ
    assert classify("2702A1B2", UDS) == ACTUATE
    assert classify("27F2A1B2", KWP) == ACTUATE
    assert classify("27", UNKNOWN) == ACTUATE
    assert classify("27F1", UNKNOWN) == ACTUATE


def test_key_request_needs_exact_provider_grant():
    tx = 0x720
    request = "2702A1B2"
    policy = SessionPolicy(protocols={tx: UDS})
    assert check_command_safety("2701", policy, tx) is None
    assert unliftable_block(request, policy, tx)
    assert "A1B2" not in check_command_safety(request, policy, tx)
    granted = policy.granting_security(tx, request)
    assert check_command_safety(request, granted, tx) is None
    assert unliftable_block(request, granted, tx + 1)
    assert unliftable_block("2702A1B3", granted, tx)


def test_security_never_uses_functional_address_even_when_registered():
    tx = 0x7DF
    policy = SessionPolicy(protocols={tx: UDS}).granting_security(tx, "2702A1B2")
    assert unliftable_block("2701", policy, tx)
    assert unliftable_block("2702A1B2", policy, tx)


def test_unsafe_does_not_bypass_key_submission_gate():
    policy = SessionPolicy(protocols={0x720: UDS})
    with pytest.raises(SafetyBlocked) as exc:
        asyncio.run(enforce_command_safety("2702A1B2", True, policy, 0x720))
    assert "A1B2" not in str(exc.value)
