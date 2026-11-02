"""Tests for keeping the ELM327 adapter's EEPROM from wearing out.

The pure policy (canlib.transport.elm327_eeprom), what the connect actually sends
(Elm327Terminal.init_elm, over a scripted channel), and the `validate` warning for
init commands that still write the EEPROM (canlib.commands.validate.init_eeprom).
"""

import asyncio
from pathlib import Path

import pytest
import yaml

from canlib.commands.validate.init_eeprom import init_eeprom_warnings
from canlib.commands.validate.pids import _elm327_warnings
from canlib.transport.elm327_eeprom import init_commands, ram_only, writes_eeprom
from canlib.transport.elm327_terminal import Elm327Terminal


class TestRamOnly:
    @pytest.mark.parametrize(
        ("command", "sent"),
        [
            ("ATSP6", "ATTP6"),
            ("at sp 7", "ATTP7"),
            ("ATSPA6", "ATTPA6"),
            ("ATSP6A", "ATTP6A"),
            ("ATSP0", "ATTP0"),
            ("ATSPC", "ATTPC"),
            ("ATM1", "ATM0"),
            ("at m 1", "ATM0"),
        ],
    )
    def test_a_saving_command_becomes_its_ram_only_form(self, command, sent):
        assert ram_only(command) == sent

    @pytest.mark.parametrize(
        "command", ["ATTP6", "ATM0", "ATS0", "ATST96", "ATSP00", "ATSPD", "ATSH7E4", "ATPPS"]
    )
    def test_anything_else_is_sent_as_written(self, command):
        assert ram_only(command) == command


class TestWritesEeprom:
    @pytest.mark.parametrize(
        "command",
        [
            "ATSP6",
            "ATSP00",
            "ATM1",
            "ATPP0C SV 23",
            "at pp 0c on",
            "ATSD01",
            "ATCV1240",
            "STWBR 115200",
            "STSAVCAL",
        ],
    )
    def test_writes(self, command):
        assert writes_eeprom(command)

    @pytest.mark.parametrize("command", ["ATTP6", "ATM0", "ATPPS", "ATS0", "ATZ", "ATST96"])
    def test_does_not_write(self, command):
        assert not writes_eeprom(command)

    @pytest.mark.parametrize("command", ["ATSP6", "ATSPA7", "ATM1"])
    def test_what_the_connect_sends_never_writes(self, command):
        assert not writes_eeprom(ram_only(command))


class TestInitCommands:
    def test_memory_goes_off_before_the_profile_init(self):
        assert init_commands("ATSP6;ATS0;ATAL;ATST96;") == [
            "ATM0",
            "ATTP6",
            "ATS0",
            "ATAL",
            "ATST96",
        ]

    def test_memory_goes_off_again_after_a_reset_in_the_init(self):
        assert init_commands("ATD;ATSP7;at ws;ATE0") == [
            "ATM0",
            "ATD",
            "ATM0",
            "ATTP7",
            "at ws",
            "ATM0",
            "ATE0",
        ]

    def test_an_empty_init_still_turns_memory_off(self):
        assert init_commands(" ; ") == ["ATM0"]


class _Channel:
    """Answers every command with OK, recording what was sent."""

    transport_name = "elm327-tcp"

    def __init__(self):
        self.sent: list[str] = []
        self._q: asyncio.Queue[str] = asyncio.Queue()

    async def connect(self) -> None:
        pass

    async def send(self, text: str) -> None:
        self.sent.append(text.rstrip("\r"))
        await self._q.put("OK\r>")

    async def recv(self, timeout: float) -> str | None:
        try:
            return self._q.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0)
            return None

    async def drain(self, per_recv_timeout: float = 0.2, max_seconds: float = 1.0) -> None:
        pass

    async def close(self) -> None:
        pass


class TestConnect:
    @pytest.mark.asyncio
    async def test_the_profile_protocol_is_never_saved(self):
        ch = _Channel()
        term = Elm327Terminal(ch)
        await term.init_elm("ATSP6;ATS0;ATAL;ATST96;")
        assert ch.sent == ["ATZ", "ATM0", "ATTP6", "ATS0", "ATAL", "ATST96"]

    @pytest.mark.asyncio
    async def test_every_reconnect_turns_memory_off_again(self):
        ch = _Channel()
        term = Elm327Terminal(ch)
        for _ in range(2):
            await term.connect()
            await term.init_elm("ATSP6;")
        assert ch.sent == ["ATZ", "ATM0", "ATTP6"] * 2


class TestValidateWarning:
    def test_ram_only_commands_and_neutralised_ones_are_not_reported(self):
        assert init_eeprom_warnings("ATSP6;ATM1;ATTP7;ATS0;ATPPS;") == []

    def test_a_persistent_write_is_reported(self):
        warnings = init_eeprom_warnings("ATSP6;ATPP 0C SV 23;ATSD01;ATSP00")
        assert len(warnings) == 3
        assert "ATPP 0C SV 23" in warnings[0] and "every connect" in warnings[0]
        assert "ATSD01" in warnings[1] and "ATSP00" in warnings[2]

    @pytest.mark.parametrize("init", [None, 6, ["ATSP6"]])
    def test_a_malformed_init_is_left_to_the_type_check(self, init):
        assert init_eeprom_warnings(init) == []

    def test_it_reaches_validate_pids(self, tmp_path: Path):
        profile = {"car_model": "Car", "init": "ATSP6;ATCV1240;"}
        (tmp_path / "profile.yaml").write_text(yaml.safe_dump(profile))
        ecus_dir = tmp_path / "ecus"
        ecus_dir.mkdir()
        (ecus_dir / "bms.yaml").write_text(yaml.safe_dump({"BMS": {"tx_id": 0x7E4}}))
        (warning,) = _elm327_warnings(ecus_dir)
        assert warning.startswith("profile.yaml: init sends ATCV1240")
