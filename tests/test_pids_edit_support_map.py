"""Tests for the ``support_map:`` editor (`pids set-support-map`) and its validation."""

import argparse

import pytest
import yaml

from canlib.commands import pids as cli
from canlib.commands import validate as validate_pids
from canlib.pids_edit import PidsEditError, set_support_map
from canlib.uds.support_map import MapDecl

YAML = """\
TEST:
  tx_id: 0x700
  pids:
    22BC03:
      status: active
      notes: first
      parameters:
        A:
          expression: B10:1
    22BC04:
      status: active
      parameters: {}
"""

DECL = MapDecl(0, 4, "bit")


@pytest.fixture
def pids_dir(tmp_path):
    (tmp_path / "test.yaml").write_text(YAML)
    return tmp_path


def _pid(pids_dir, pid="22BC03"):
    return yaml.safe_load((pids_dir / "test.yaml").read_text())["TEST"]["pids"][pid]


class TestSetSupportMap:
    def test_written_as_a_one_line_flow_mapping_after_the_header(self, pids_dir):
        set_support_map("TEST", "22BC03", DECL, pids_dir=pids_dir)
        text = (pids_dir / "test.yaml").read_text()
        assert "    22BC03:\n      support_map: {at: 0, length: 4, unit: bit, order: msb}\n" in text
        assert _pid(pids_dir)["support_map"] == {
            "at": 0,
            "length": 4,
            "unit": "bit",
            "order": "msb",
        }

    def test_the_rest_of_the_file_is_untouched(self, pids_dir):
        before = (pids_dir / "test.yaml").read_text().splitlines()
        set_support_map("TEST", "22BC03", DECL, pids_dir=pids_dir)
        after = (pids_dir / "test.yaml").read_text().splitlines()
        assert [ln for ln in after if "support_map" not in ln] == before

    def test_only_the_named_pid_changes(self, pids_dir):
        set_support_map("TEST", "22BC03", DECL, pids_dir=pids_dir)
        assert "support_map" not in _pid(pids_dir, "22BC04")

    def test_replacing_leaves_exactly_one_declaration(self, pids_dir):
        set_support_map("TEST", "22BC03", DECL, pids_dir=pids_dir)
        set_support_map("TEST", "22BC03", MapDecl(2, 1, "byte"), pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text().count("support_map") == 1
        assert _pid(pids_dir)["support_map"]["unit"] == "byte"

    def test_clearing_restores_the_original_bytes(self, pids_dir):
        before = (pids_dir / "test.yaml").read_text()
        set_support_map("TEST", "22BC03", DECL, pids_dir=pids_dir)
        set_support_map("TEST", "22BC03", None, pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == before

    def test_clearing_an_unset_map_is_a_no_op(self, pids_dir):
        before = (pids_dir / "test.yaml").read_text()
        set_support_map("TEST", "22BC03", None, pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == before

    def test_pid_lookup_is_case_insensitive(self, pids_dir):
        set_support_map("test", "22bc03", DECL, pids_dir=pids_dir)
        assert "support_map" in _pid(pids_dir)

    def test_unknown_pid_leaves_the_file_alone(self, pids_dir):
        before = (pids_dir / "test.yaml").read_text()
        with pytest.raises(PidsEditError, match="not found"):
            set_support_map("TEST", "9999", DECL, pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == before

    def test_unknown_ecu(self, pids_dir):
        with pytest.raises(PidsEditError):
            set_support_map("NOPE", "22BC03", DECL, pids_dir=pids_dir)


class _Args(argparse.Namespace):
    def __init__(self, pids_dir, **kw):
        super().__init__(
            ecu="TEST",
            pid="22BC03",
            at=0,
            length=4,
            unit="bit",
            clear=False,
            dir=pids_dir,
            no_validate=True,
        )
        self.__dict__.update(kw)


class TestCommand:
    def test_declares(self, pids_dir, capsys):
        assert cli.cmd_set_support_map(_Args(pids_dir)) == 0
        assert "support_map" in _pid(pids_dir)
        assert "support_map" in capsys.readouterr().out

    def test_clear(self, pids_dir):
        cli.cmd_set_support_map(_Args(pids_dir))
        cli.cmd_set_support_map(_Args(pids_dir, clear=True, at=None, length=None, unit=None))
        assert "support_map" not in _pid(pids_dir)

    @pytest.mark.parametrize("missing", ["at", "length", "unit"])
    def test_needs_the_whole_layout(self, pids_dir, missing):
        with pytest.raises(SystemExit, match="--at, --length and --unit"):
            cli.cmd_set_support_map(_Args(pids_dir, **{missing: None}))

    def test_a_bad_layout_is_refused_before_touching_the_file(self, pids_dir):
        before = (pids_dir / "test.yaml").read_text()
        with pytest.raises(SystemExit, match="length"):
            cli.cmd_set_support_map(_Args(pids_dir, length=9))
        assert (pids_dir / "test.yaml").read_text() == before

    def test_the_schema_gate_can_veto_and_revert(self, pids_dir, monkeypatch):
        before = (pids_dir / "test.yaml").read_text()
        monkeypatch.setattr(cli, "_schema_validate", lambda p: (False, "  ERROR: nope"))
        with pytest.raises(SystemExit):
            cli.cmd_set_support_map(_Args(pids_dir, no_validate=False))
        assert (pids_dir / "test.yaml").read_text() == before


class TestValidation:
    def _validate(self, tmp_path, *fields: str):
        p = tmp_path / "e.yaml"
        pid_fields = "".join(f"      {f}\n" for f in fields)
        p.write_text(
            "ECU:\n"
            "  tx_id: 0x770\n"
            "  pids:\n"
            '    "22BC03":\n'
            "      status: active\n"
            f"{pid_fields}"
            "      parameters:\n"
            "        A:\n"
            "          expression: B10:1\n"
        )
        errors, _warnings, _stats = validate_pids.validate_ecu_file(p, validate_pids.load_schema())
        return errors

    def test_a_valid_declaration_passes(self, tmp_path):
        assert (
            self._validate(tmp_path, "support_map: {at: 0, length: 4, unit: bit, order: msb}") == []
        )

    def test_order_is_optional(self, tmp_path):
        assert self._validate(tmp_path, "support_map: {at: 0, length: 4, unit: byte}") == []

    @pytest.mark.parametrize(
        "field, needle",
        [
            ("support_map: {at: 0, length: 9, unit: bit}", "length"),
            ("support_map: {at: 0, length: 4, unit: nibble}", "unit"),
            ("support_map: {length: 4, unit: bit}", "needs 'at'"),
            ("support_map: {at: 0, length: 4, unit: bit, width: 2}", "unknown"),
            ("support_map: yes", "mapping"),
        ],
    )
    def test_a_malformed_declaration_is_an_error(self, tmp_path, field, needle):
        errs = self._validate(tmp_path, field)
        assert any("support_map" in e and needle in e for e in errs), errs

    def test_the_field_is_known_to_the_schema(self):
        assert "support_map" in validate_pids.load_schema()["optional_pid_fields"]
