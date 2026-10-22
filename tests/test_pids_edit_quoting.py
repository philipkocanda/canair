"""Tests for mapping-key and value quoting across the text editors.

A key or value is written bare only when *both* project readers — PyYAML and the
ruamel round-trip reader (YAML 1.1) — read it back as exactly the string written.
Otherwise ``0203`` is the octal int 131, ``0700`` int 448, ``1E00`` the float
1.0 and ``220E01`` (to ruamel) the float 2200.0.
"""

from __future__ import annotations

import textwrap
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from canlib.pids_edit import (
    PidsEditError,
    _text,
    add_research_entry,
    append_iocontrol_discoveries_block,
    append_routines_block,
    promote_discovery,
    update_routines_field,
)
from canlib.yaml_rt import round_trip_yaml


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "_meta.yaml").write_text('car_model: "Test"\ninit: "ATSP6;"\n')
    (tmp_path / "test.yaml").write_text(textwrap.dedent(body))
    return tmp_path


def _read_both(d: Path):
    text = (d / "test.yaml").read_text()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a MantissaNoDot warning is a failure too
        return yaml.safe_load(text)["ECU"], round_trip_yaml().load(text)["ECU"]


def _hit(**kw):
    return SimpleNamespace(**{"nrc": None, "nrc_desc": None, "response_hex": "", **kw})


BASE = """\
    ECU:
      tx_id: 0x7A0
    """


class TestScannerKeys:
    @pytest.mark.parametrize("rid", [0x0203, 0x0700, 0x1E00])
    def test_routine_keys_read_back_as_strings(self, tmp_path, rid):
        d = _write(tmp_path, BASE)
        append_routines_block("ECU", [_hit(rid=rid, response_hex="7101")], pids_dir=d)
        for ecu in _read_both(d):
            assert [str(k) for k in ecu["routines"]] == [f"{rid:04X}"]
            assert all(isinstance(k, str) for k in ecu["routines"])

    def test_an_unambiguous_key_stays_bare(self, tmp_path):
        d = _write(tmp_path, BASE)
        append_routines_block("ECU", [_hit(rid=0xB0A1)], pids_dir=d)
        assert "\n    B0A1:\n" in (d / "test.yaml").read_text()

    def test_a_quoted_rid_can_be_edited_and_rescanned(self, tmp_path):
        d = _write(tmp_path, BASE)
        append_routines_block("ECU", [_hit(rid=0x0203), _hit(rid=0x0300)], pids_dir=d)
        update_routines_field("ECU", "0203", "notes", "hand note", pids_dir=d)
        append_routines_block("ECU", [_hit(rid=0x0300, response_hex="7103")], pids_dir=d)
        py, _ = _read_both(d)
        assert py["routines"]["0203"]["notes"] == "hand note"  # survived the merge
        assert set(py["routines"]) == {"0203", "0300"}

    def test_single_quoted_existing_entries_merge(self, tmp_path):
        d = _write(
            tmp_path,
            """\
            ECU:
              iocontrol_discoveries:
                '0700':
                  response: "6F070001"
                  notes: "kept"
            """,
        )
        append_iocontrol_discoveries_block("ECU", [_hit(did=0x0800)], pids_dir=d)
        py, _ = _read_both(d)
        assert set(py["iocontrol_discoveries"]) == {"0700", "0800"}
        assert py["iocontrol_discoveries"]["0700"]["notes"] == "kept"


class TestPromoteDiscoveryKeys:
    DISC = """\
        ECU:
          iocontrol_discoveries:
            "0700":
              response: "6F070001"
              notes: ""
            "1E00":
              response: "6F1E0002"
              notes: ""

          iocontrol:
            "0700":
              label: Curated
        """

    def test_refuses_a_quoted_curated_duplicate(self, tmp_path):
        d = _write(tmp_path, self.DISC)
        with pytest.raises(PidsEditError, match="already exists"):
            promote_discovery("ECU", "0700", "Again", pids_dir=d)

    def test_promoted_key_reads_back_as_a_string(self, tmp_path):
        d = _write(tmp_path, self.DISC)
        promote_discovery("ECU", "1E00", "Lamp", pids_dir=d)
        for ecu in _read_both(d):
            assert [str(k) for k in ecu["iocontrol"]] == ["0700", "1E00"]
            assert all(isinstance(k, str) for k in ecu["iocontrol"])
            assert ecu["iocontrol"]["1E00"]["label"] == "Lamp"

    def test_discoveries_before_iocontrol_keep_offsets_right(self, tmp_path):
        d = _write(tmp_path, self.DISC)
        promote_discovery("ECU", "1E00", "Lamp", pids_dir=d)
        text = (d / "test.yaml").read_text()
        assert text.index('    "1E00":\n      label: Lamp') > text.index("  iocontrol:")


class TestResearchTargetQuoting:
    def test_exponent_shaped_target_reads_back_as_a_string(self, tmp_path):
        d = _write(tmp_path, BASE)
        add_research_entry("ECU", type="decode", target="220E01", status="open", pids_dir=d)
        for ecu in _read_both(d):
            assert ecu["research"][0]["target"] == "220E01"
            assert isinstance(ecu["research"][0]["target"], str)


class TestBothReadersCheck:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        _text._plain_reads_back.cache_clear()
        yield
        _text._plain_reads_back.cache_clear()

    @pytest.mark.parametrize("key", ["0x10", "1_000", "0700", "0b101", "1E00", "220E01"])
    def test_the_ruamel_half_alone_catches_coercing_keys(self, monkeypatch, key):
        # A lenient stand-in for PyYAML leaves the decision to the round-trip reader.
        monkeypatch.setattr(yaml, "safe_load", lambda doc: {key: "x"})
        assert _text._needs_key_quoting(key)

    def test_a_round_trip_reader_error_means_quote(self, monkeypatch):
        from ruamel.yaml.error import YAMLError as RuamelYAMLError

        class _Broken:
            def load(self, doc):
                raise RuamelYAMLError("boom")

        monkeypatch.setattr("canlib.yaml_rt.round_trip_yaml", lambda: _Broken())
        assert _text._needs_key_quoting("2101")
        assert _text._yaml_reinterprets("plain")

    @pytest.mark.parametrize("key", ["2101", "22B004", "010C", "0o17"])
    def test_keys_both_readers_agree_on_stay_bare(self, key):
        assert _text._key_token(key) == key

    @pytest.mark.parametrize("value", ["220E01", "1e3", "0700", "a #b", "yes", "~"])
    def test_values_quoted_when_either_reader_differs(self, value):
        assert _text._format_label(value).startswith('"')

    def test_a_flow_list_item_with_a_comma_is_quoted(self):
        assert _text._format_inline_list_field("", "k", ["A,B", "C"]) == ['k: ["A,B", C]']
