"""The functional discovery header names the service its request asks for."""

from __future__ import annotations

import pytest

from canair.live.discover.functional import describe_request


@pytest.mark.parametrize(
    ("request_hex", "expected"),
    [
        ("3E00", "TesterPresent (zeroSubFunction)"),
        ("3E80", "TesterPresent (zeroSubFunction, suppressPosRspMsgIndication)"),
        ("1001", "DiagnosticSessionControl (defaultSession)"),
        ("22F190", "ReadDataByIdentifier"),
        ("FF", ""),
        ("", ""),
        ("XYZ", ""),
    ],
)
def test_describe_request(request_hex: str, expected: str) -> None:
    assert describe_request(request_hex) == expected
