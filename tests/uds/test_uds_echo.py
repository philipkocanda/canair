"""canair.uds.echo: what a positive reply must repeat of its request."""

from __future__ import annotations

import pytest

from canair.uds.echo import reply_check, request_echo
from canair.uds.protocol import KWP, UDS, UNKNOWN


class TestSubFunctionEchoOnUds:
    """A UDS sub-function service's reply repeats the sub-function, without bit 7."""

    @pytest.mark.parametrize(
        ("request_hex", "echo"),
        [
            ("1003", "03"),  # DiagnosticSessionControl
            ("1083", "03"),  # ... with the suppress-positive-response bit
            ("3E00", "00"),  # TesterPresent
            ("1902FF", "02"),  # ReadDTCInformation reportDTCByStatusMask
            ("8501", "01"),  # ControlDTCSetting on
            ("3101F000", "01F000"),  # RoutineControl: the type and the routine id
            ("3181F000AA", "01F000"),  # ... suppress bit, and an option record
            ("3103FF00", "03FF00"),
        ],
    )
    def test_the_echo(self, request_hex, echo):
        assert request_echo(request_hex, UDS) == (int(request_hex[:2], 16), bytes.fromhex(echo))

    @pytest.mark.parametrize("protocol", [KWP, UNKNOWN, None])
    @pytest.mark.parametrize("request_hex", ["1003", "3E00", "3101F000", "1902FF"])
    def test_other_protocols_are_not_judged(self, request_hex, protocol):
        # KWP2000 ECUs need not repeat a session type, and 31 takes a 1-byte local id there.
        assert request_echo(request_hex, protocol) is None

    def test_a_routine_request_without_its_whole_id_is_not_judged(self):
        assert reply_check("3101F0", UDS) is None

    def test_identifier_reads_keep_their_echo_on_every_protocol(self):
        assert request_echo("22F190", UDS) == (0x22, bytes.fromhex("F190"))
        assert request_echo("22F190") == (0x22, bytes.fromhex("F190"))
        assert request_echo("2101", UDS) == (0x21, bytes.fromhex("01"))

    def test_a_service_without_a_sub_function_echo_is_not_judged(self):
        assert request_echo("2FBC1000", UDS) is None  # IOControl callers pass the DID
        assert request_echo("14FFFFFF", UDS) is None
