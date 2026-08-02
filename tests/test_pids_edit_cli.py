"""Tests for `canair pids` CLI orchestration (snapshot -> edit -> validate gate)."""

import pytest

from canlib.commands import pids as cli


@pytest.fixture
def pids_dir(tmp_path):
    (tmp_path / "e.yaml").write_text(
        "TESTECU:\n  tx_id: 0x700\n  pids:\n    2101:\n      status: active\n      parameters: {}\n"
    )
    return tmp_path


def test_guarded_commits_when_gate_passes(pids_dir, monkeypatch):
    fp = pids_dir / "e.yaml"
    monkeypatch.setattr(cli, "_schema_validate", lambda p: (True, ""))
    cli._guarded(
        "TESTECU", pids_dir, lambda: fp.write_text(fp.read_text() + "# ok\n"), validate=True
    )
    assert "# ok" in fp.read_text()


def test_guarded_rolls_back_when_gate_fails(pids_dir, monkeypatch):
    fp = pids_dir / "e.yaml"
    original = fp.read_text()
    monkeypatch.setattr(cli, "_schema_validate", lambda p: (False, "  ERROR: bad"))
    with pytest.raises(SystemExit):
        cli._guarded("TESTECU", pids_dir, lambda: fp.write_text("CORRUPTED"), validate=True)
    assert fp.read_text() == original  # reverted


def test_guarded_skips_gate_when_disabled(pids_dir, monkeypatch):
    fp = pids_dir / "e.yaml"

    def _boom(_):
        raise AssertionError("validator should not run when validate=False")

    monkeypatch.setattr(cli, "_schema_validate", _boom)
    cli._guarded("TESTECU", pids_dir, lambda: fp.write_text("whatever"), validate=False)
    assert fp.read_text() == "whatever"


class _Args:
    def __init__(self, **kw):
        self.ecu = "TESTECU"
        self.dir = None
        self.mode = self.target_address = self.source_address = None
        self.fc_id = self.rx_id = None
        self.no_validate = False
        self.__dict__.update(kw)


def test_set_addressing_writes_block(pids_dir):
    import canlib.yaml_io as yaml_io

    rc = cli.cmd_set_addressing(
        _Args(dir=pids_dir, mode="normal_extended_11bit", target_address="0x12", rx_id="0x708")
    )
    assert rc == 0
    doc = yaml_io.safe_load((pids_dir / "e.yaml").read_text())
    assert doc["TESTECU"]["addressing"]["mode"] == "normal_extended_11bit"
    assert doc["TESTECU"]["addressing"]["target_address"] == 0x12
    assert doc["TESTECU"]["rx_id"] == 0x708


def test_set_addressing_requires_a_field(pids_dir):
    with pytest.raises(SystemExit, match="nothing to set"):
        cli.cmd_set_addressing(_Args(dir=pids_dir))


def test_set_addressing_bad_hex(pids_dir):
    with pytest.raises(SystemExit, match="fc-id"):
        cli.cmd_set_addressing(_Args(dir=pids_dir, fc_id="nothex"))


class _RangeArgs:
    def __init__(self, ranges, **kw):
        self.ecu = "TESTECU"
        self.dir = None
        self.ranges = ranges
        self.no_validate = True  # skip the active-profile schema gate in unit tests
        self.__dict__.update(kw)


def test_set_iocontrol_ranges_writes_field(pids_dir):
    import canlib.yaml_io as yaml_io

    rc = cli.cmd_set_iocontrol_ranges(_RangeArgs(["b000-bfff", "C000-C0FF"], dir=pids_dir))
    assert rc == 0
    doc = yaml_io.safe_load((pids_dir / "e.yaml").read_text())
    # Normalized to canonical upper hex.
    assert doc["TESTECU"]["iocontrol_scan_ranges"] == ["B000-BFFF", "C000-C0FF"]


def test_set_iocontrol_ranges_rejects_bad_range(pids_dir):
    from canlib.pids_edit import PidsEditError

    with pytest.raises((PidsEditError, SystemExit)):
        cli.cmd_set_iocontrol_ranges(_RangeArgs(["not-a-range"], dir=pids_dir))
