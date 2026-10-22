"""Tests for the surgical editors' block boundaries, line endings and parse guards.

The text editors in :mod:`canlib.pids_edit` cut and append blocks by indentation.
A block ends after its own last line and blank separation — never inside the
comments that head the *next* key — and an edit keeps the file's line endings.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from canlib.pids_edit import (
    PidsEditError,
    delete_parameter,
    delete_pid,
    delete_research_entry,
    remove_identity_field,
    update_iocontrol_field,
    upsert_parameter,
)


def _write(tmp_path: Path, body: str, *, newline: str = "\n") -> Path:
    """Write the ECU file; an indented (triple-quoted) ``body`` is dedented first.

    A flush-left ``body`` is written verbatim — ``textwrap.dedent`` would blank
    the whitespace-only lines some tests are about.
    """
    (tmp_path / "_meta.yaml").write_text('car_model: "Test"\ninit: "ATSP6;"\n')
    text = textwrap.dedent(body) if body[:1].isspace() else body
    (tmp_path / "test.yaml").write_text(text, newline=newline)
    return tmp_path


def _text(d: Path) -> str:
    return (d / "test.yaml").read_text()


def _load(d: Path) -> dict:
    return yaml.safe_load(_text(d))["ECU"]


IOCONTROL_BANNER = """\
ECU:
  tx_id: 0x7A0
  pids:
    2101:
      status: active
      parameters: {}

    2102:
      status: ignored
      parameters: {}

  # ─── IOControl (service 2F) ───
  # A banner describing the section below.
  iocontrol:
    B003:
      label: Unknown
"""


class TestRemovalKeepsTheNextKeysComments:
    def test_rm_pid_before_a_section_banner(self, tmp_path):
        d = _write(tmp_path, IOCONTROL_BANNER)
        delete_pid("ECU", "2102", pids_dir=d)
        assert _text(d) == IOCONTROL_BANNER.replace(
            "\n    2102:\n      status: ignored\n      parameters: {}\n", ""
        )

    def test_rm_pid_with_a_comment_directly_above_the_next_sibling(self, tmp_path):
        d = _write(
            tmp_path,
            """\
            ECU:
              pids:
                2101:
                  status: active
                # about 2102
                2102:
                  status: active
            """,
        )
        delete_pid("ECU", "2101", pids_dir=d)
        assert _text(d) == "ECU:\n  pids:\n    # about 2102\n    2102:\n      status: active\n"

    def test_rm_param_before_a_shallower_pid_banner(self, tmp_path):
        d = _write(
            tmp_path,
            """\
            ECU:
              pids:
                22B003:
                  status: active
                  parameters:
                    KEEP:
                      expression: B4
                    DROP:
                      expression: B18
                      notes: >-
                        a folded note
                # ─── TPMS configuration DIDs ───
                # Static sensor registration.
                22C001:
                  status: static
            """,
        )
        delete_parameter("ECU", "22B003", "DROP", pids_dir=d)
        text = _text(d)
        assert "      expression: B4\n    # ─── TPMS configuration DIDs ───\n" in text
        assert "    # Static sensor registration.\n    22C001:" in text
        assert "DROP" not in text

    def test_a_deeper_commented_out_field_goes_with_its_block(self, tmp_path):
        d = _write(
            tmp_path,
            """\
            ECU:
              pids:
                2101:
                  status: active
                  # period: 500
                2102:
                  status: active
            """,
        )
        delete_pid("ECU", "2101", pids_dir=d)
        assert _text(d) == "ECU:\n  pids:\n    2102:\n      status: active\n"

    def test_rm_identity_field_before_a_section_comment(self, tmp_path):
        d = _write(
            tmp_path,
            """\
            ECU:
              identity:
                part_number: "123"
                notes: >-
                  a note

              # --- pids ---
              pids:
                2101:
                  status: active
            """,
        )
        remove_identity_field("ECU", "notes", pids_dir=d)
        assert _text(d) == (
            'ECU:\n  identity:\n    part_number: "123"\n\n'
            "  # --- pids ---\n  pids:\n    2101:\n      status: active\n"
        )

    def test_rm_the_sole_identity_field_drops_the_section_but_not_the_comment(self, tmp_path):
        d = _write(
            tmp_path,
            """\
            ECU:
              tx_id: 0x7A0

              identity:
                notes: only field

              # --- pids ---
              pids:
                2101:
                  status: active
            """,
        )
        remove_identity_field("ECU", "notes", pids_dir=d)
        assert "identity" not in _load(d)
        assert _text(d) == (
            "ECU:\n  tx_id: 0x7A0\n\n  # --- pids ---\n  pids:\n    2101:\n      status: active\n"
        )

    def test_rm_the_last_research_item_keeps_the_next_comment(self, tmp_path):
        d = _write(
            tmp_path,
            """\
            ECU:
              research:
                - type: decode
                  target: "2101"
                  status: open

              # --- iocontrol ---
              iocontrol:
                B003:
                  label: Unknown
            """,
        )
        delete_research_entry("ECU", "2101", pids_dir=d)
        assert _text(d) == (
            "ECU:\n  # --- iocontrol ---\n  iocontrol:\n    B003:\n      label: Unknown\n"
        )

    def test_rm_a_research_item_keeps_the_comment_heading_the_next_item(self, tmp_path):
        d = _write(
            tmp_path,
            """\
            ECU:
              research:
                - type: decode
                  target: A
                  status: open
                # the next lead
                - type: decode
                  target: B
                  status: open
            """,
        )
        delete_research_entry("ECU", "A", pids_dir=d)
        assert _text(d) == (
            "ECU:\n  research:\n    # the next lead\n"
            "    - type: decode\n      target: B\n      status: open\n"
        )


class TestRemovalGaps:
    def test_rm_last_pid_at_eof_without_a_trailing_newline(self, tmp_path):
        d = _write(
            tmp_path,
            "ECU:\n  pids:\n    2101:\n      status: active\n\n    2102:\n      status: active",
        )
        delete_pid("ECU", "2102", pids_dir=d)
        assert _text(d) == "ECU:\n  pids:\n    2101:\n      status: active\n"

    def test_whitespace_only_separator_lines(self, tmp_path):
        d = _write(
            tmp_path,
            "ECU:\n  pids:\n    2101:\n      status: active\n  \n"
            "    2102:\n      status: active\n    \n  # banner\n  research:\n"
            "    - type: decode\n      target: A\n      status: open\n",
        )
        delete_pid("ECU", "2102", pids_dir=d)
        text = _text(d)
        assert text == (
            "ECU:\n  pids:\n    2101:\n      status: active\n    \n  # banner\n  research:\n"
            "    - type: decode\n      target: A\n      status: open\n"
        )
        assert yaml.safe_load(text)

    def test_rm_first_item_of_a_blank_separated_research_list(self, tmp_path):
        d = _write(
            tmp_path,
            """\
            ECU:
              research:
                - type: decode
                  target: A
                  status: open

                - type: decode
                  target: B
                  status: open
            """,
        )
        delete_research_entry("ECU", "A", pids_dir=d)
        assert _text(d).startswith("ECU:\n  research:\n    - type: decode\n      target: B\n")

    def test_rm_the_last_research_item_after_whitespace_only_lines(self, tmp_path):
        d = _write(
            tmp_path,
            "ECU:\n  tx_id: 0x7A0\n  \n\n  research:\n"
            "    - type: decode\n      target: A\n      status: open\n",
        )
        delete_research_entry("ECU", "A", pids_dir=d)
        assert _text(d) == "ECU:\n  tx_id: 0x7A0\n"


class TestAppendStopsBeforeTheNextKeysComments:
    def test_new_param_lands_above_the_next_pid_banner(self, tmp_path):
        d = _write(
            tmp_path,
            """\
            ECU:
              pids:
                2101:
                  status: active
                  parameters:
                    A:
                      expression: B4

                # ─── next group ───
                2102:
                  status: active
            """,
        )
        upsert_parameter("ECU", "2101", "B", "B5", pids_dir=d)
        assert "        B:\n          expression: B5\n\n    # ─── next group ───\n" in _text(d)

    def test_new_iocontrol_field_lands_inside_its_entry(self, tmp_path):
        d = _write(
            tmp_path,
            """\
            ECU:
              iocontrol:
                B001:
                  label: First
                # about B002
                B002:
                  label: Second
            """,
        )
        update_iocontrol_field("ECU", "B001", "verified", True, pids_dir=d)
        assert "      label: First\n      verified: true\n    # about B002\n" in _text(d)


class TestLineEndings:
    def test_crlf_file_stays_crlf(self, tmp_path):
        d = _write(tmp_path, IOCONTROL_BANNER, newline="\r\n")
        delete_pid("ECU", "2102", pids_dir=d)
        upsert_parameter("ECU", "2101", "A", "B4", notes="x " * 80, pids_dir=d)
        raw = (d / "test.yaml").read_bytes()
        assert raw.count(b"\n") == raw.count(b"\r\n") > 10

    def test_crlf_survives_a_reverted_edit(self, tmp_path):
        d = _write(tmp_path, IOCONTROL_BANNER, newline="\r\n")
        before = (d / "test.yaml").read_bytes()
        with pytest.raises(PidsEditError):
            delete_pid("ECU", "9999", pids_dir=d)
        assert (d / "test.yaml").read_bytes() == before

    def test_scanner_writers_keep_crlf(self, tmp_path):
        d = _write(tmp_path, IOCONTROL_BANNER, newline="\r\n")
        update_iocontrol_field("ECU", "B003", "label", "Lamp", pids_dir=d)
        raw = (d / "test.yaml").read_bytes()
        assert b"label: Lamp\r\n" in raw
        assert raw.count(b"\n") == raw.count(b"\r\n")

    def test_lf_file_stays_lf(self, tmp_path):
        d = _write(tmp_path, IOCONTROL_BANNER)
        delete_pid("ECU", "2102", pids_dir=d)
        assert b"\r" not in (d / "test.yaml").read_bytes()


PARAM_FILE = """\
ECU:
  pids:
    2101:
      status: active
      parameters:
        BASE:
          expression: B4
          unit: &volts V
        ALIASED:
          expression: 'B5'
          unit: *volts
          send_to: some/topic
"""


class TestUpsertUnchangedExpressionGuard:
    def test_a_block_with_an_alias_does_not_crash(self, tmp_path):
        # The block alone can't parse (its anchor lives in another param).
        d = _write(tmp_path, PARAM_FILE)
        upsert_parameter("ECU", "2101", "ALIASED", "B6", pids_dir=d)
        p = _load(d)["pids"][2101]["parameters"]["ALIASED"]
        assert p["expression"] == "B6"
        assert p["unit"] == "V"

    def test_a_non_mapping_param_is_a_clean_error(self, tmp_path):
        d = _write(tmp_path, "ECU:\n  pids:\n    2101:\n      parameters:\n        P: 5\n")
        before = _text(d)
        with pytest.raises(PidsEditError):
            upsert_parameter("ECU", "2101", "P", "B4", pids_dir=d)
        assert _text(d) == before

    def test_an_int_stored_expression_is_rewritten_as_a_string(self, tmp_path):
        d = _write(
            tmp_path,
            "ECU:\n  pids:\n    2101:\n      parameters:\n        P:\n          expression: 5\n",
        )
        upsert_parameter("ECU", "2101", "P", "5", pids_dir=d)
        assert _load(d)["pids"][2101]["parameters"]["P"]["expression"] == "5"

    def test_clear_and_skip_in_one_edit(self, tmp_path):
        d = _write(tmp_path, PARAM_FILE.replace("unit: *volts", "unit: mV"))
        upsert_parameter("ECU", "2101", "ALIASED", "B5", send_to="", unit="A", pids_dir=d)
        text = _text(d)
        assert "          expression: 'B5'\n" in text  # byte-identical: not re-rendered
        assert "send_to" not in text
        assert _load(d)["pids"][2101]["parameters"]["ALIASED"]["unit"] == "A"

    def test_an_indented_first_line_note_no_longer_reverts(self, tmp_path):
        d = _write(tmp_path, PARAM_FILE)
        note = "  - first bullet\n  - second bullet"
        upsert_parameter("ECU", "2101", "BASE", "B4", notes=note, pids_dir=d)
        assert _load(d)["pids"][2101]["parameters"]["BASE"]["notes"] == note
