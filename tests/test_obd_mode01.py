"""OBD-II mode 01 through the query mini-language, the planner and the ELM327 engine.

Four hex digits read as a service-22 DID (``0101`` -> ``220101``), and DIDs 0100-010F
are real on some ECUs, so mode 01 is asked for with the explicit ``SERVICE/ID`` form
(``01/0D``) rather than by changing what a bare token means.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from canlib.modes.multi_exec import build_query_plan
from canlib.modes.multi_parse import parse_sub_commands
from canlib.query import (
    QueryError,
    explicit_request,
    inferred_request,
    looks_like_pid,
    parse_query,
    parse_selector,
    pids_named_by,
)
from canlib.transport.elm327_terminal import Elm327Terminal
from canlib.uds.obd import supported_pids
from canlib.uds.parse import request_echo

from ._fakes import QueuedChannel


class TestExplicitToken:
    @pytest.mark.parametrize(
        ("token", "code"),
        [("01/0D", "010D"), ("01/0d", "010D"), ("09/02", "0902"), ("22/0101", "220101")],
    )
    def test_names_one_request(self, token, code):
        assert explicit_request(token) == code

    def test_a_bare_token_is_not_explicit(self):
        assert explicit_request("010D") is None

    @pytest.mark.parametrize("token", ["04/00", "2E/F190", "01/0C0D", "01/D", "22/01", "01/ZZ"])
    def test_malformed_or_unsupported_is_refused(self, token):
        with pytest.raises(QueryError):
            explicit_request(token)

    def test_the_parser_refuses_it_up_front(self):
        with pytest.raises(QueryError, match="not one of"):
            parse_selector("BMS:04/00")

    def test_looks_like_a_pid_in_the_ecu_slot(self):
        assert looks_like_pid("01/0D")
        assert not looks_like_pid("BMS")


class TestInference:
    @pytest.mark.parametrize(
        ("token", "code"),
        [
            ("01", "2101"),
            ("B001", "22B001"),
            ("0101", "220101"),  # a DID, as before: never OBD-II mode 01
            ("22BC03", "22BC03"),
            ("2101", "2101"),
            ("01/01", "0101"),
        ],
    )
    def test_request_for_an_undefined_token(self, token, code):
        assert inferred_request(token) == code

    def test_non_hex_is_none(self):
        assert inferred_request("SOC") is None


class TestPidsNamedBy:
    KEYS = ("0101", "220101", "22BC03", "2101")

    def test_explicit_is_exact(self):
        assert pids_named_by("01/01", self.KEYS) == ["0101"]
        assert pids_named_by("22/0101", self.KEYS) == ["220101"]

    def test_an_exact_key_wins_over_a_suffix(self):
        # Before, 0101 also dragged in 220101, which merely ends with it.
        assert pids_named_by("0101", self.KEYS) == ["0101"]

    def test_suffix_still_matches_without_an_exact_key(self):
        assert pids_named_by("BC03", self.KEYS) == ["22BC03"]


class TestCaptureMatching:
    def test_explicit_selector_matches_only_its_request(self):
        q = parse_query("BMS:01/00")
        assert q.matches("BMS", "0100")
        assert not q.matches("BMS", "220100")
        assert not q.matches("BMS", "010001")

    def test_bare_selector_keeps_prefix_and_suffix_matching(self):
        q = parse_query("BMS:0100")
        assert q.matches("BMS", "0100") and q.matches("BMS", "220100")


class TestQueryPlan:
    INFO: ClassVar[dict] = {
        "tx_id": 0x7E4,
        "multi_did": False,
        "pids": {
            "2101": {"parameters": {}, "status": "active", "swept": True},
            "220101": {"parameters": {}, "status": "active", "swept": True},
        },
    }

    def _plan(self, filters):
        return [(code, unmapped) for code, _info, unmapped in build_query_plan(self.INFO, filters)]

    def test_an_undefined_mode_01_pid_is_sent_as_mode_01(self, capsys):
        assert self._plan(["01/0D"]) == [("010D", True)]
        assert "010D not in ecus/" in capsys.readouterr().out

    def test_a_bare_four_digit_token_still_means_a_did(self):
        assert self._plan(["0102"]) == [("220102", True)]

    def test_explicit_did_picks_the_defined_one(self):
        assert self._plan(["22/0101"]) == [("220101", False)]

    def test_a_defined_mode_01_pid_is_decoded(self):
        info = {**self.INFO, "pids": {**self.INFO["pids"], "0101": {"parameters": {}}}}
        plan = build_query_plan(info, ["01/01"])
        assert [(c, u) for c, _i, u in plan] == [("0101", False)]

    def test_the_query_step_parser_accepts_it(self):
        steps = parse_sub_commands(["query BMS:01/0D,2101"])
        assert steps == [{"type": "query", "ecu": "BMS", "pids": ["01/0D", "2101"]}]

    def test_space_instead_of_colon_is_caught(self):
        with pytest.raises(ValueError, match="looks like a PID"):
            parse_sub_commands(["query BMS 01/0D"])


class TestEchoOnTheElm327Wire:
    """The BMS probe regression, end to end through the shared ELM327 engine."""

    def _send(self, *chunks):
        channel = QueuedChannel(*chunks)
        channel.after_drain = ["ELM327 v2.3\r\r>"]  # the resync probe's answer
        term = Elm327Terminal(channel, timeout=2.0)
        sid, echo = request_echo("0100")
        return asyncio.run(term.send_uds("0100", expected_sid=sid, expected_echo=echo))

    def test_the_echo_is_not_stored_as_data(self):
        resp = self._send("0100\r410080000001\r\r>")
        assert resp["ok"] is True
        assert resp["hex"] == "410080000001"

    def test_a_reply_for_another_pid_is_stale(self):
        # A desynchronised pipe hands back the previous PID's reply.
        resp = self._send("0100\r412080018001\r\r>")
        assert resp["ok"] is False
        assert resp["error_kind"] == "stale"


def test_support_bitmap():
    assert supported_pids(0x00, bytes.fromhex("80000001")) == [0x01, 0x20]
    assert supported_pids(0x20, bytes.fromhex("80018001")) == [0x21, 0x30, 0x31, 0x40]
