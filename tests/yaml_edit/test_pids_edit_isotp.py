"""``canair pids set-isotp``: an ECU's own isotp: block, edited surgically."""

from __future__ import annotations

import argparse

import pytest
import yaml

from canair.cli.commands.pids import ecu_facts as cli
from canair.cli.profile_context import active
from canair.yaml_edit.pids import PidsEditError, set_ecu_isotp

PLAIN = """# A comment that must survive.
EWP:
  tx_id: 0x746
  can_bus: [P-CAN]
  pids:
    21F2:
      status: active
      parameters: {}
"""

ADDRESSED = """DME:
  tx_id: 0x6F1
  addressing:
    mode: normal_extended_11bit
    target_address: 0x12

  can_bus: [PT-CAN]
"""


@pytest.fixture
def ecus(tmp_path):
    (tmp_path / "ewp.yaml").write_text(PLAIN)
    (tmp_path / "dme.yaml").write_text(ADDRESSED)
    return tmp_path


def _ecu(ecus, name: str, file: str) -> dict:
    return yaml.safe_load((ecus / file).read_text())[name]


def test_a_new_block_goes_after_tx_id_and_nothing_else_changes(ecus):
    set_ecu_isotp("EWP", {"blocksize": 8}, pids_dir=ecus, profile=active())
    text = (ecus / "ewp.yaml").read_text()
    assert text == PLAIN.replace("0x746\n", "0x746\n  isotp:\n    blocksize: 8\n")


def test_a_new_block_goes_after_the_addressing_block(ecus):
    set_ecu_isotp("DME", {"blocksize": 2}, pids_dir=ecus, profile=active())
    text = (ecus / "dme.yaml").read_text()
    assert text.index("target_address") < text.index("isotp:") < text.index("can_bus")
    assert _ecu(ecus, "DME", "dme.yaml")["isotp"] == {"blocksize": 2}


def test_fields_merge_and_keep_their_canonical_order(ecus):
    set_ecu_isotp("EWP", {"rx_consecutive_frame_timeout": 500}, pids_dir=ecus, profile=active())
    set_ecu_isotp("EWP", {"blocksize": 8}, pids_dir=ecus, profile=active())
    block = _ecu(ecus, "EWP", "ewp.yaml")["isotp"]
    assert list(block.items()) == [("blocksize", 8), ("rx_consecutive_frame_timeout", 500)]


def test_clearing_the_last_field_removes_the_block(ecus):
    set_ecu_isotp("EWP", {"blocksize": 8}, pids_dir=ecus, profile=active())
    set_ecu_isotp("EWP", {"blocksize": None}, pids_dir=ecus, profile=active())
    assert (ecus / "ewp.yaml").read_text() == PLAIN


def test_a_profile_wide_field_is_refused(ecus):
    with pytest.raises(PidsEditError, match="cannot be set per ECU"):
        set_ecu_isotp("EWP", {"tx_padding": 0x55}, pids_dir=ecus, profile=active())
    assert (ecus / "ewp.yaml").read_text() == PLAIN


def test_the_command_sets_and_clears(ecus, monkeypatch):
    monkeypatch.setattr(
        "canair.yaml_edit.ecufile.validate_pids_file", lambda p, *a, **__: (True, "")
    )
    args = argparse.Namespace(
        ecu="EWP",
        dir=ecus,
        no_validate=False,
        blocksize="8",
        rx_flowcontrol_timeout=None,
        rx_consecutive_frame_timeout="300",
    )
    assert cli.cmd_set_isotp(args) == 0
    assert _ecu(ecus, "EWP", "ewp.yaml")["isotp"] == {
        "blocksize": 8,
        "rx_consecutive_frame_timeout": 300,
    }
    args.blocksize, args.rx_consecutive_frame_timeout = "clear", "clear"
    assert cli.cmd_set_isotp(args) == 0
    assert "isotp" not in _ecu(ecus, "EWP", "ewp.yaml")
