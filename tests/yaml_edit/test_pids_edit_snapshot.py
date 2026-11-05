"""Tests for the ``snapshot_only:``/``snapshot_length:`` editor (`pids set-snapshot-only`),
its validation, and what the flag hides from sweeps and the device profile."""

import argparse

import pytest
import yaml

from canair.cli.commands import pids as cli
from canair.core.edit_errors import EditError
from canair.ecus.pids import build_ecu_index, pid_ships, pid_swept
from canair.validation.ecu_file import validate_ecu_file
from canair.validation.schema_fields import load_schema
from canair.yaml_edit.pids import PidsEditError, set_snapshot_only

YAML = """\
TEST:
  tx_id: 0x733
  pids:
    224710:
      status: draft
      notes: environment data
      parameters:
        A:
          expression: B05
    22C350:
      status: active
      parameters: {}
"""


@pytest.fixture
def pids_dir(tmp_path):
    (tmp_path / "test.yaml").write_text(YAML)
    return tmp_path


def _pid(pids_dir, pid="224710"):
    pids = yaml.safe_load((pids_dir / "test.yaml").read_text())["TEST"]["pids"]
    return next(v for k, v in pids.items() if str(k) == pid)


class TestSetSnapshotOnly:
    def test_written_after_the_header(self, pids_dir):
        set_snapshot_only("TEST", "224710", True, length=9, pids_dir=pids_dir)
        text = (pids_dir / "test.yaml").read_text()
        assert "    224710:\n      snapshot_only: true\n      snapshot_length: 9\n" in text
        assert _pid(pids_dir)["snapshot_only"] is True
        assert _pid(pids_dir)["snapshot_length"] == 9

    def test_without_a_length_writes_only_the_flag(self, pids_dir):
        set_snapshot_only("TEST", "224710", True, pids_dir=pids_dir)
        assert "snapshot_length" not in _pid(pids_dir)

    def test_re_marking_keeps_the_length(self, pids_dir):
        set_snapshot_only("TEST", "224710", True, length=9, pids_dir=pids_dir)
        set_snapshot_only("TEST", "224710", True, pids_dir=pids_dir)
        assert _pid(pids_dir)["snapshot_length"] == 9
        assert (pids_dir / "test.yaml").read_text().count("snapshot_only") == 1

    def test_clearing_removes_both_and_restores_the_bytes(self, pids_dir):
        before = (pids_dir / "test.yaml").read_text()
        set_snapshot_only("TEST", "224710", True, length=9, pids_dir=pids_dir)
        set_snapshot_only("TEST", "224710", False, pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == before

    def test_only_the_named_pid_changes(self, pids_dir):
        set_snapshot_only("TEST", "224710", True, pids_dir=pids_dir)
        assert "snapshot_only" not in _pid(pids_dir, "22C350")

    @pytest.mark.parametrize("pid", ["2101", "1904582C00", "22471"])
    def test_refused_on_a_non_did_key(self, pids_dir, pid):
        before = (pids_dir / "test.yaml").read_text()
        with pytest.raises(PidsEditError, match="service-22"):
            set_snapshot_only("TEST", pid, True, pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == before

    def test_a_length_needs_the_flag(self, pids_dir):
        with pytest.raises(PidsEditError, match="only goes with"):
            set_snapshot_only("TEST", "224710", False, length=9, pids_dir=pids_dir)

    def test_a_zero_length_is_refused(self, pids_dir):
        with pytest.raises(PidsEditError, match=">= 1"):
            set_snapshot_only("TEST", "224710", True, length=0, pids_dir=pids_dir)

    def test_unknown_pid_leaves_the_file_alone(self, pids_dir):
        before = (pids_dir / "test.yaml").read_text()
        with pytest.raises(PidsEditError, match="not found"):
            set_snapshot_only("TEST", "22FFFF", True, pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == before


class _Args(argparse.Namespace):
    def __init__(self, pids_dir, **kw):
        super().__init__(
            ecu="TEST", pid="224710", length=None, clear=False, dir=pids_dir, no_validate=True
        )
        self.__dict__.update(kw)


class TestCommand:
    def test_marks(self, pids_dir, capsys):
        assert cli.cmd_set_snapshot_only(_Args(pids_dir, length=9)) == 0
        assert _pid(pids_dir)["snapshot_length"] == 9
        assert "snapshot_only -> true, snapshot_length 9" in capsys.readouterr().out

    def test_clear(self, pids_dir):
        cli.cmd_set_snapshot_only(_Args(pids_dir))
        cli.cmd_set_snapshot_only(_Args(pids_dir, clear=True))
        assert "snapshot_only" not in _pid(pids_dir)

    def test_the_schema_gate_can_veto_and_revert(self, pids_dir, monkeypatch):
        before = (pids_dir / "test.yaml").read_text()
        monkeypatch.setattr(
            "canair.validation.pids.validate_pids_file", lambda p, *a: (False, "  ERROR: nope")
        )
        with pytest.raises(EditError):
            cli.cmd_set_snapshot_only(_Args(pids_dir, no_validate=False))
        assert (pids_dir / "test.yaml").read_text() == before


class TestValidation:
    def _validate(self, tmp_path, *fields: str, pid: str = "224710"):
        p = tmp_path / "e.yaml"
        pid_fields = "".join(f"      {f}\n" for f in fields)
        p.write_text(
            "ECU:\n"
            "  tx_id: 0x733\n"
            "  pids:\n"
            f'    "{pid}":\n'
            "      status: draft\n"
            f"{pid_fields}"
            "      parameters:\n"
            "        A:\n"
            "          expression: B05\n"
        )
        errors, _warnings, _stats = validate_ecu_file(p, load_schema())
        return errors

    def test_a_valid_declaration_passes(self, tmp_path):
        assert self._validate(tmp_path, "snapshot_only: true", "snapshot_length: 9") == []

    @pytest.mark.parametrize(
        "fields, needle",
        [
            (["snapshot_only: yes please"], "snapshot_only must be a boolean"),
            (["snapshot_length: 0"], "snapshot_length must be an int >= 1"),
            (["snapshot_length: true"], "snapshot_length must be an int >= 1"),
            (["snapshot_length: nine"], "snapshot_length must be an int >= 1"),
        ],
    )
    def test_malformed_fields_are_errors(self, tmp_path, fields, needle):
        errs = self._validate(tmp_path, *fields)
        assert any(needle in e for e in errs), errs

    def test_snapshot_only_needs_a_did_key(self, tmp_path):
        errs = self._validate(tmp_path, "snapshot_only: true", pid="2101")
        assert any("service-22 DID key" in e for e in errs), errs

    def test_the_fields_are_known_to_the_schema(self):
        fields = load_schema()["optional_pid_fields"]
        assert "snapshot_only" in fields and "snapshot_length" in fields


class TestVisibility:
    """A snapshot-only DID answers a live read with an NRC, so nothing polls or ships it."""

    def test_neither_swept_nor_shipped_whatever_its_status(self):
        pid = {"status": "active", "snapshot_only": True, "parameters": {}}
        assert not pid_swept(pid)
        assert not pid_ships(pid)

    def test_without_the_flag_the_status_decides(self):
        assert pid_swept({"status": "active"}) and pid_ships({"status": "active"})
        assert pid_swept({"status": "draft"}) and not pid_ships({"status": "draft"})

    def test_the_ecu_index_carries_it(self):
        ecus = yaml.safe_load(YAML)
        ecus["TEST"]["pids"][224710]["snapshot_only"] = True
        entry = build_ecu_index({"ecus": ecus})["TEST"]["pids"]["224710"]
        assert entry["snapshot_only"] is True
        assert entry["swept"] is False and entry["shipped"] is False
