"""Tests for canair.ecus.memory — the memory: section model and its checks."""

from __future__ import annotations

import pytest

from canair.ecus.memory import ecu_memory, memory_format_of, section_problems
from canair.ecus.pid_status import pid_ships
from canair.uds.memory import KWP_FORMAT, parse_format


class TestModel:
    def test_kwp_ecu_has_a_fixed_form(self):
        assert memory_format_of({"identity": {"id_protocol": "KWP2000"}}) == KWP_FORMAT

    def test_uds_ecu_needs_a_declared_format(self):
        assert memory_format_of({"identity": {"id_protocol": "UDS"}}) is None
        assert memory_format_of({"memory": {"format": "13"}}) == parse_format("13")

    def test_regions(self):
        mem = ecu_memory(
            {
                "memory": {
                    "format": "24",
                    "max_block": 128,
                    "regions": [{"start": 0x10, "end": 0x1F, "result": "readable"}],
                }
            }
        )
        assert mem is not None and mem.max_block == 128 and mem.regions[0].readable

    @pytest.mark.parametrize(
        "section",
        [
            {"format": "2"},
            {"max_block": 0},
            {"max_block": 5000},
            {"session": 3},
            {"regions": [{"start": 2, "end": 1, "result": "readable"}]},
            {"regions": [{"start": 0, "end": 1, "result": "maybe"}]},
        ],
    )
    def test_problems(self, section):
        errors, _ = section_problems(section)
        assert errors


def test_a_memory_pid_never_ships():
    pid = {"status": "active", "parameters": {"V": {"expression": "B04", "verified": True}}}
    assert pid_ships(pid, "22B003")
    assert not pid_ships(pid, "2324FEDE00000004")
