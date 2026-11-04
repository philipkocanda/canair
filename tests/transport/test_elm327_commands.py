"""The ELM327 command facts shared by the terminal, addressing cache and EEPROM guard."""

from __future__ import annotations

from canlib.transport import elm327_addressing, elm327_eeprom, elm327_terminal
from canlib.transport.elm327_commands import RESET_COMMANDS, canonical
from canlib.transport.elm327_pipe import PENDING_RE


def test_canonical_drops_whitespace_and_case():
    assert canonical("at sh 7e0") == "ATSH7E0"
    assert canonical(" at\tz\r") == "ATZ"


def test_one_reset_set_and_one_canonical_form_everywhere():
    for module in (elm327_addressing, elm327_eeprom, elm327_terminal):
        assert module.RESET_COMMANDS is RESET_COMMANDS
        assert module.canonical is canonical


def test_the_pending_pattern_is_built_from_the_nrc_constants():
    assert PENDING_RE.pattern == "7F[0-9A-Fa-f]{2}78"
    assert PENDING_RE.fullmatch("7F2278") and not PENDING_RE.fullmatch("7F2231")
